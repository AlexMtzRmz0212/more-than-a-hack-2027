"""A first look at ball kinematics by possession state, on the public 5 minutes.

Exploration only: the public ground truth is used to *describe* the data, never to train or tune.
Writes aggregates only (no raw tracking) to presentation/glimpse.json.

    python presentation/make_glimpse.py
"""

import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "starter"))
from games import GAME, GAMES, require  # noqa: E402
from baseline import team_of_each_track  # noqa: E402

GT_PATH = os.path.join(ROOT, "evaluation", "gt", "BAR-ATH_possession_gt_public.csv")
FIRST, LAST = 11185, 18684  # public ground-truth window (video frames)
FPS = GAMES[GAME]["fps"]
LENGTH, WIDTH = GAMES[GAME]["pitch"]
SPEED_BINS = [0, 2, 4, 6, 8, 10, 12, 15, 20, 30]


def parse_xy(group):
    if group[0] == "N":
        return None
    x, y = group.split(",")[:2]
    return float(x) * LENGTH, float(y) * WIDTH


def main():
    meta_path, raw_path = require(["atd_meta", "atd_raw"])
    teams = team_of_each_track(meta_path)

    n = LAST - FIRST + 1
    ball = np.full((n, 2), np.nan)
    nearest = np.full(n, np.nan)
    nearest_team = np.full(n, -1)
    pressure = np.full(n, np.nan)  # second-nearest player of the other team

    with open(raw_path) as handle:
        for line in handle:
            parts = line.split(":")
            if len(parts) != 3:
                continue
            frame = int(parts[0]) - 1  # same convention as starter/baseline.py
            if frame < FIRST or frame > LAST:
                continue
            b = parse_xy(parts[2])
            if b is None:
                continue
            i = frame - FIRST
            ball[i] = b
            best = [(np.inf, -1)]
            per_team = {0: np.inf, 1: np.inf}
            for slot, group in enumerate(parts[1].split(";")):
                p = parse_xy(group)
                if p is None:
                    continue
                d = ((p[0] - b[0]) ** 2 + (p[1] - b[1]) ** 2) ** 0.5
                t = int(teams[slot])
                per_team[t] = min(per_team[t], d)
                if d < best[0][0]:
                    best = [(d, t)]
            nearest[i], nearest_team[i] = best[0]
            if best[0][1] in (0, 1):
                pressure[i] = per_team[1 - best[0][1]]

    labels = []
    with open(GT_PATH) as handle:
        next(handle)
        for line in handle:
            f, lab = line.strip().split(",")
            if FIRST <= int(f) <= LAST:
                labels.append(lab)
    labels = np.array(labels)

    # Ball speed from consecutive tracked positions, m/s; light 5-frame median against jitter.
    step = np.linalg.norm(np.diff(ball, axis=0), axis=1) * FPS
    speed = np.concatenate(([np.nan], step))
    padded = np.pad(speed, 2, constant_values=np.nan)
    windows = np.lib.stride_tricks.sliding_window_view(padded, 5)
    with np.errstate(all="ignore"):
        import warnings
        warnings.simplefilter("ignore")
        speed = np.nanmedian(windows, axis=1)
    speed[speed > 45] = np.nan  # physically impossible jumps = tracker switching objects

    # Possession spells in the ground truth, and how long the ball stays "glued" in each.
    out = {"window": [FIRST, LAST], "frames": int(n), "states": {}}
    for state in ("H", "A", "D"):
        m = labels == state
        tracked = m & ~np.isnan(ball[:, 0])
        s = speed[m & ~np.isnan(speed)]
        d = nearest[tracked]
        pr = pressure[m & ~np.isnan(pressure)]
        hist, _ = np.histogram(s, bins=SPEED_BINS)
        out["states"][state] = {
            "frames": int(m.sum()),
            "ball_tracked_pct": round(100.0 * tracked.sum() / max(m.sum(), 1), 1),
            "speed_median": round(float(np.median(s)), 2) if s.size else None,
            "speed_p25": round(float(np.percentile(s, 25)), 2) if s.size else None,
            "speed_p75": round(float(np.percentile(s, 75)), 2) if s.size else None,
            "nearest_median_m": round(float(np.median(d)), 2) if d.size else None,
            "within_2m_pct": round(100.0 * float(np.mean(d < 2.0)), 1) if d.size else None,
            "opponent_pressure_median_m": round(float(np.median(pr)), 2) if pr.size else None,
            "speed_hist_pct": [round(100.0 * h / max(s.size, 1), 1) for h in hist],
        }

    # Nearest-player team vs GT team, when the ball is alive and seen.
    alive = np.isin(labels, ["H", "A"]) & (nearest_team >= 0)
    gt_team = np.where(labels == "H", 0, 1)
    out["nearest_player_agrees_pct"] = round(
        100.0 * float(np.mean(nearest_team[alive] == gt_team[alive])), 1)
    out["speed_bins"] = SPEED_BINS

    with open(os.path.join(HERE, "glimpse.json"), "w") as handle:
        json.dump(out, handle, indent=2)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
