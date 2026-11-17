"""Review possession predictions next to the tactical video. Local only, standard library only.

    python player/serve.py            # then open http://127.0.0.1:8765  (opens itself)
    python player/serve.py --check    # verify the CSV parsing against the raw files, then exit

What it serves
--------------
  /                      player/index.html
  /video                 the tactical video, with HTTP Range support so seeking in a 5 GB file
                         does not download the file
  /api/info              fps, frame count, public window, and every CSV found in evaluation/results/
  /api/labels/<name>     one label per video frame, as a string: character i is the label of
                         frame i (H, A, D or X). 'ground truth' is the public ground truth.

Nothing is copied, converted or written. The video is read in place from data/ (or MTAH_DATA_DIR,
exactly as games.py resolves it), the server binds to 127.0.0.1 only, and it only answers requests
whose Host header is a loopback name, so a web page you happen to have open cannot reach the match
data through it.

CSV rules are the scorer's (evaluation/possession_metrics.py): header 'frame,label', one row per
video frame, 'frame' counting 0..n-1 in order, labels in H/A/D/X. A file that breaks them is
listed in the UI with the reason instead of being silently dropped or mis-aligned.
"""

import argparse
import csv
import glob
import json
import os
import re
import sys
import threading
import webbrowser
import xml.etree.ElementTree as ET
from array import array
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import games  # noqa: E402  (needs the path above)

LABELS = "HADX"
GT_NAME = "ground truth (public)"
GT_PATH = os.path.join(ROOT, "evaluation", "gt", "BAR-ATH_possession_gt_public.csv")
GT_META = os.path.join(ROOT, "evaluation", "gt", "BAR-ATH_possession_gt_public_meta.json")
RESULTS_DIR = os.path.join(ROOT, "evaluation", "results")
CHUNK = 1 << 20  # 1 MiB per write when streaming the video
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")


# --------------------------------------------------------------------------------------------
# CSV -> one character per frame
# --------------------------------------------------------------------------------------------

def read_labels(path, n_frames):
    """Return (labels_string, None) for a valid file, or (None, reason).

    Mirrors possession_metrics.validate_submission: exact row count, 'frame' == row index,
    no blank cells, labels in H/A/D/X (case and whitespace tolerated). Uses csv, not pandas, so
    the player runs on a bare Python install.
    """
    try:
        with open(path, newline="", encoding="utf-8-sig") as handle:
            reader = csv.reader(handle)
            header = [cell.strip().lower() for cell in next(reader, [])]
            if "frame" not in header or "label" not in header:
                return None, "expected columns 'frame' and 'label', got {}".format(header)
            i_frame, i_label = header.index("frame"), header.index("label")
            out = []
            for row_number, row in enumerate(reader):
                if len(row) <= max(i_frame, i_label):
                    return None, "row {} is too short: {!r}".format(row_number, row)
                try:
                    frame = int(row[i_frame])
                except ValueError:
                    return None, "row {}: 'frame' is not an integer: {!r}".format(
                        row_number, row[i_frame])
                if frame != row_number:
                    return None, "'frame' column is not 0..n-1 in order (row {} says {})".format(
                        row_number, frame)
                label = row[i_label].strip().upper()
                if not label:
                    return None, "empty label at frame {}; write X to abstain".format(frame)
                if label not in LABELS:
                    return None, "label {!r} at frame {} is outside H/A/D/X".format(label, frame)
                out.append(label)
    except (OSError, UnicodeDecodeError, csv.Error) as error:
        return None, "could not read: {}".format(error)
    if len(out) != n_frames:
        return None, "row count {} != {} video frames".format(len(out), n_frames)
    return "".join(out), None


class LabelStore:
    """Parsed CSVs, re-read only when the file's size or mtime changes."""

    def __init__(self, n_frames):
        self.n_frames = n_frames
        self._cache = {}
        self._lock = threading.Lock()

    def get(self, path):
        stat = os.stat(path)
        key = (stat.st_mtime_ns, stat.st_size)
        with self._lock:
            hit = self._cache.get(path)
            if hit and hit[0] == key:
                return hit[1], hit[2]
        labels, reason = read_labels(path, self.n_frames)
        with self._lock:
            self._cache[path] = (key, labels, reason)
        return labels, reason


def natural_key(path):
    """Sort 'smooth25' before 'smooth50' before 'smooth125', not alphabetically."""
    name = os.path.basename(path).lower()
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name)]


def discover(store):
    """[{name, path, ok, reason}] for ground truth first, then every results/*.csv in natural
    order: baseline_raw, baseline_smooth25, baseline_smooth50, baseline_smooth125."""
    found = []
    if os.path.isfile(GT_PATH):
        found.append((GT_NAME, GT_PATH, True))
    for path in sorted(glob.glob(os.path.join(RESULTS_DIR, "*.csv")), key=natural_key):
        found.append((os.path.splitext(os.path.basename(path))[0], path, False))
    sources = []
    for name, path, is_gt in found:
        _, reason = store.get(path)
        sources.append({"name": name, "path": path, "gt": is_gt, "ok": reason is None,
                        "reason": reason})
    return sources


# --------------------------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------------------------

class Tracking:
    """Ball and player positions plus the pitch-to-pixel matrix, read lazily from data/.

    Nothing is loaded up front. A background thread scans each file once and records where every
    frame's line starts (checking, on the way, that line i really is frame i: ATD ids run 1..n and
    homography rows 0..n-1, as DATA.md says). After that a request for 100 frames reads and parses
    only those 100 lines, so the 195 MB tracking file never sits in memory.
    """

    def __init__(self, atd_raw, atd_meta, homography, n_frames):
        self.paths = (atd_raw, atd_meta, homography)
        self.n = n_frames
        self.ready = threading.Event()
        self.error = None
        self.slot_team = []
        self._atd = self._hom = None
        threading.Thread(target=self._build, daemon=True).start()

    @staticmethod
    def _index(path, header, first_id, separator, n):
        offsets, position = array("Q"), 0
        with open(path, "rb") as handle:
            if header:
                position = len(handle.readline())
            for line in handle:
                ident = int(line[:line.index(separator)])
                if ident != first_id + len(offsets):
                    raise ValueError("{}: line {} is frame {}, expected {}".format(
                        os.path.basename(path), len(offsets), ident, first_id + len(offsets)))
                offsets.append(position)
                position += len(line)
        offsets.append(position)
        if len(offsets) - 1 != n:
            raise ValueError("{}: {} frames, expected {}".format(
                os.path.basename(path), len(offsets) - 1, n))
        return offsets

    def _build(self):
        try:
            atd_raw, atd_meta, homography = self.paths
            root = ET.parse(atd_meta).getroot()
            home = root.find(".//Score").attrib["idLocalTeam"]
            players = sorted(root.iter("Player"), key=lambda p: int(p.attrib["id"]))
            if [int(p.attrib["id"]) for p in players] != list(range(1, len(players) + 1)):
                raise ValueError("ATD player ids are not 1..n")
            self.slot_team = ["H" if p.attrib["teamId"] == home else "A" for p in players]
            self._atd = self._index(atd_raw, False, 1, b":", self.n)
            self._hom = self._index(homography, True, 0, b",", self.n)
        except Exception as error:  # reported to the page, never raised into the server
            self.error = "{}: {}".format(type(error).__name__, error)
        finally:
            self.ready.set()

    @staticmethod
    def _lines(path, offsets, first, stop):
        with open(path, "rb") as handle:
            handle.seek(offsets[first])
            return handle.read(offsets[stop] - offsets[first]).decode("ascii").splitlines()

    def chunk(self, start, count):
        """Frames start..start+count-1 as [{'h': 9 floats | None, 'b': [x,y] | None,
        'p': [[x, y, 'H'|'A'], ...]}]. Coordinates are the ATD's own, normalised 0-1."""
        stop = min(start + count, self.n)
        atd = self._lines(self.paths[0], self._atd, start, stop)
        hom = self._lines(self.paths[2], self._hom, start, stop)
        frames = []
        for atd_line, hom_line in zip(atd, hom):
            _, players, ball = atd_line.split(":")
            people = []
            for team, entry in zip(self.slot_team, players.split(";")):
                x, y = entry.split(",")[:2]
                if x != "NaN" and y != "NaN":
                    people.append([round(float(x), 5), round(float(y), 5), team])
            bx, by = ball.split(",")[:2]
            cells = hom_line.split(",")
            matrix = None
            if cells[11].strip() == "1":
                matrix = [float(c) for c in cells[1:10]]
            frames.append({"h": matrix, "p": people,
                           "b": None if bx == "NaN" or by == "NaN"
                           else [round(float(bx), 5), round(float(by), 5)]})
        return frames


def parse_range(header, size):
    """(start, end) inclusive for a single 'bytes=' range, None for 'no range', False if bad."""
    if not header:
        return None
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
    if not match or (not match.group(1) and not match.group(2)):
        return False
    if match.group(1):
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else size - 1
    else:  # suffix: the last N bytes
        length = int(match.group(2))
        start, end = max(size - length, 0), size - 1
    end = min(end, size - 1)
    if start > end or start >= size:
        return False
    return start, end


class Handler(BaseHTTPRequestHandler):
    server_version = "possession-player"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # the video makes hundreds of range requests; keep quiet
        pass

    # -- plumbing ---------------------------------------------------------------------------
    def _host_ok(self):
        value = self.headers.get("Host") or ""
        host = value.split("]")[0] + "]" if value.startswith("[") else value.rsplit(":", 1)[0]
        return host in LOOPBACK_HOSTS

    def _send_bytes(self, body, content_type, status=HTTPStatus.OK, head=False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _send_json(self, payload, status=HTTPStatus.OK, head=False):
        self._send_bytes(json.dumps(payload).encode("utf-8"), "application/json", status, head)

    def _fail(self, status, message, head=False):
        self._send_json({"error": message}, status, head)

    # -- routes -----------------------------------------------------------------------------
    def do_HEAD(self):
        self._route(head=True)

    def do_GET(self):
        self._route(head=False)

    def _route(self, head):
        if not self._host_ok():
            return self._fail(HTTPStatus.FORBIDDEN, "loopback hosts only", head)
        path = urlparse(self.path).path
        app = self.server.app
        try:
            if path in ("/", "/index.html"):
                with open(os.path.join(HERE, "index.html"), "rb") as handle:
                    return self._send_bytes(handle.read(), "text/html; charset=utf-8", head=head)
            if path == "/video":
                return self._video(head)
            if path == "/api/info":
                return self._send_json(app.info(), head=head)
            if path.startswith("/api/labels/"):
                return self._labels(unquote(path[len("/api/labels/"):]), head)
            if path == "/api/tracking":
                return self._tracking(parse_qs(urlparse(self.path).query), head)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return  # the browser cancelled (seek, tab closed); nothing to report
        self._fail(HTTPStatus.NOT_FOUND, "not found", head)

    def _labels(self, name, head):
        source = next((s for s in self.server.app.sources() if s["name"] == name), None)
        if source is None:
            return self._fail(HTTPStatus.NOT_FOUND, "no such prediction: " + name, head)
        labels, reason = self.server.app.store.get(source["path"])
        if reason:
            return self._fail(HTTPStatus.UNPROCESSABLE_ENTITY, reason, head)
        self._send_json({"name": name, "n": len(labels), "labels": labels}, head=head)

    def _tracking(self, query, head):
        tracking = self.server.app.tracking
        if tracking is None:
            return self._fail(HTTPStatus.NOT_FOUND, "tracking files not found in data/", head)
        try:
            start = int(query.get("start", ["0"])[0])
            count = min(int(query.get("count", ["100"])[0]), 400)
        except ValueError:
            return self._fail(HTTPStatus.BAD_REQUEST, "start and count must be integers", head)
        if not 0 <= start < tracking.n or count < 1:
            return self._fail(HTTPStatus.BAD_REQUEST, "start out of range", head)
        if not tracking.ready.wait(120):
            return self._fail(HTTPStatus.SERVICE_UNAVAILABLE, "still indexing tracking", head)
        if tracking.error:
            return self._fail(HTTPStatus.INTERNAL_SERVER_ERROR, tracking.error, head)
        self._send_json({"start": start, "frames": tracking.chunk(start, count)}, head=head)

    def _video(self, head):
        path = self.server.app.video
        size = os.path.getsize(path)
        wanted = parse_range(self.headers.get("Range"), size)
        if wanted is False:
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", "bytes */{}".format(size))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        start, end = wanted if wanted else (0, size - 1)
        self.send_response(HTTPStatus.PARTIAL_CONTENT if wanted else HTTPStatus.OK)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if wanted:
            self.send_header("Content-Range", "bytes {}-{}/{}".format(start, end, size))
        self.end_headers()
        if head:
            return
        with open(path, "rb") as handle:
            handle.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                block = handle.read(min(CHUNK, remaining))
                if not block:
                    break
                self.wfile.write(block)
                remaining -= len(block)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # Seeking aborts in-flight range requests all the time; that is not an error.
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError,
                                          ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


class App:
    def __init__(self, video, n_frames, fps, tracking=None):
        self.video = video
        self.n_frames = n_frames
        self.fps = fps
        self.tracking = tracking      # None when the ATD / homography files are not in data/
        self.store = LabelStore(n_frames)

    def sources(self):
        return discover(self.store)

    def info(self):
        with open(GT_META, encoding="utf-8") as handle:
            meta = json.load(handle)
        window = meta["public_window"]
        return {
            "game": games.GAME,
            "fps": self.fps,
            "n_frames": self.n_frames,
            "video_bytes": os.path.getsize(self.video),
            "public_window": [window["first_video_frame"], window["last_video_frame"]],
            "label_names": {k: v for k, v in meta["labels"].items()},
            "gt_name": GT_NAME,
            "tracking": {"available": self.tracking is not None,
                         "pitch_m": list(games.GAMES[games.GAME]["pitch"])},
            "sources": [{k: s[k] for k in ("name", "gt", "ok", "reason")}
                        for s in self.sources()],
        }


def build_app(video_override=None):
    game = games.GAMES[games.GAME]
    with open(GT_META, encoding="utf-8") as handle:
        meta = json.load(handle)
    # games.py, the metadata and the CSVs must agree before anything is shown against a video.
    if meta["fps"] != game["fps"] or meta["n_video_frames"] != game["n_video_frames"]:
        raise SystemExit("games.py and {} disagree on fps / frame count".format(GT_META))
    video = video_override or games.require(["video"])[0]
    if not os.path.isfile(video):
        raise SystemExit("video not found: {}".format(video))
    tracking = None
    if not video_override:  # the tracking belongs to the tactical video, so not to another file
        paths = games.asset_paths()
        wanted = [paths[k] for k in ("atd_raw", "atd_meta", "homography")]
        if all(os.path.isfile(p) for p in wanted):
            tracking = Tracking(*wanted, game["n_video_frames"])
    return App(video, game["n_video_frames"], game["fps"], tracking)


def check(app):
    """Independent re-read of every CSV (plain line splitting) against what the API would send."""
    bad = 0
    for source in app.sources():
        if not source["ok"]:
            print("SKIP  {}  ({})".format(source["name"], source["reason"]))
            continue
        labels, _ = app.store.get(source["path"])
        with open(source["path"], encoding="utf-8-sig") as handle:
            lines = handle.read().splitlines()[1:]
        mismatches = 0
        for i, line in enumerate(lines):
            frame, label = line.split(",")
            if int(frame) != i or labels[i] != label.strip().upper():
                mismatches += 1
        counts = {c: labels.count(c) for c in LABELS if labels.count(c)}
        print("{}  {}  {} rows, {} mismatches, {}".format(
            "OK  " if not mismatches and len(lines) == len(labels) == app.n_frames else "FAIL",
            source["name"], len(lines), mismatches, counts))
        bad += bool(mismatches)
    return 1 if bad else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--video", help="video file (default: the tactical video from games.py)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    parser.add_argument("--check", action="store_true",
                        help="verify CSV parsing against the raw files, then exit")
    args = parser.parse_args()

    try:
        app = build_app(args.video)
    except IOError as error:  # games.require: match data not where it should be
        raise SystemExit(str(error))
    if args.check:
        sys.exit(check(app))

    server = Server(("127.0.0.1", args.port), Handler)
    server.app = app
    url = "http://127.0.0.1:{}/".format(args.port)
    sources = app.sources()
    print("Possession player  {}".format(url))
    print("  video       {}".format(app.video))
    for source in sources:
        print("  {}  {}{}".format("ok  " if source["ok"] else "SKIP", source["name"],
                                  "" if source["ok"] else "  -- " + source["reason"]))
    print("Leave this window open while you use the player. Ctrl+C stops it.")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
