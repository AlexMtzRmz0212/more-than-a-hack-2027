# Possession review player

Watch the tactical video next to every prediction in `evaluation/results/` and the public ground
truth, frame by frame. Local only: nothing is uploaded, converted or copied.

## Run it (Windows)

1. Put the match data in `data\` as described in [data/README.md](../data/README.md). The player
   needs `data\tactical\BAR-ATH_tactical.mp4`. (`MTAH_DATA_DIR` works too, as everywhere else.)
2. Double-click **`player\run_player.bat`**. A black window opens and your browser opens at
   <http://127.0.0.1:8765/>.

   Or, from PowerShell in the repository root:

   ```powershell
   python player\serve.py
   ```
3. Use **desktop Chrome or Edge**. Keep the black window open; close it (or press Ctrl+C) to stop.

Needs only Python 3 — no `pip install`.

| option | effect |
|---|---|
| `--port 9000` | use another port if 8765 is taken |
| `--no-browser` | do not open the browser tab |
| `--video PATH` | play another file (must be the tactical timeline) |
| `--check` | re-read every CSV independently and compare it with what the player would show, then exit |

## What you see

- **Video** with play / pause, a frame slider, **−1 frame / +1 frame**, ±1 s (25 frames), speed
  0.25–2×, and *Go to frame*. The frame number is on the video and above the slider.
- **Labels at this frame**, one row per file: the public ground truth first, then each CSV in
  `evaluation/results/` in natural order (raw, 25, 50, 125; new files appear on page reload). Each prediction row also says whether it
  matches the ground truth at this frame.
- **Ground truth unavailable.** Only frames 11185–18684 have public labels. Everywhere else the
  ground-truth row says *Unavailable* (it is `X` in the CSV) and the prediction rows say *no GT
  here*. The slider highlights the public window; **Start of public ground truth** jumps to it.
- **Stripes on the video**: one see-through horizontal stripe per row (ground truth on top, then
  each file in the same order as the list): `H` = blue (Barcelona), `A` = red (Athletic),
  `D` = grey (dead ball), `X` = yellow (no label), with the letter and file name at the right.
  The same colors are used for the chips, strips and legend. The **Colors on video** checkbox
  hides them.
- **Ball and players on the picture**, from the provided tracking data (ATD) placed with the
  per-frame homography. Barcelona players get a blue ring, Athletic players a red one. The ball is a
  yellow dot in a ring. A dashed
  white line joins the ball to the nearest player, who gets a white ring (what the nearest-player
  baselines credit), and the readout under the slider gives that player's team and distance. The **Ball** and **Players** checkboxes hide them.
  - The ATD is Metrica's automatic output, not corrected: the ball is missing on about 43% of
    frames (the video then says *ball not tracked on this frame*) and can sit off the real ball, so
    treat the overlay as the tracker's opinion, not the truth.
  - The homography maps the ground, so a ball in the air is drawn off its real spot in the picture.
  - Tracking is read from `data\` on demand, 100 frames at a time, and only for the tactical video.
    Without the ATD and homography files the overlay is simply absent.
- The page fits the window with no scrolling; the video takes the height left over. The accuracy
  notes open as a popover. Below 1000 px wide it stacks and scrolls instead.
- A **strip** under each row shows ±250 frames around the current one, and ◂ ▸ jump to the previous
  / next change in that row, landing on the first frame of the new label.

Keys: `Space` play/pause · `←` `→` one frame · `Shift`+`←` `→` 25 frames.

Files in `evaluation/results/` that break the scorer's format (row count, `frame` not `0..n-1`,
blank or unknown labels) are listed with the reason instead of being shown misaligned.

## Frame accuracy: what you can rely on

The video is constant 25 fps (199 319 frames, H.264), and CSV row *i* is video frame *i*.

| situation | accuracy |
|---|---|
| paused, stepping, seeking, jumping | **exact.** Seeks aim at the middle of the frame, and labels change only when the browser reports the new picture as presented. |
| playing | labels follow the frame the decoder reports, so they never belong to a frame that was not on screen, but they can **trail the picture by up to one frame (40 ms)**, and on a slow machine the readout can skip frames. Pause and step to judge a transition. |
| browser without `requestVideoFrameCallback`, or one where it stays silent | the frame is **estimated from the playback clock, ±1 frame while playing**. The readout says *estimated*. A seek that lands without a decoder report is also labelled this way, though it is still aimed at the exact frame. |

If the browser shows a different frame from the one requested, the number on screen is the one it
actually shows, and a notice says so. The video's frame count is checked against the CSVs when it
loads, with a warning on mismatch.

Not used: the broadcast video (not aligned to the submission rows) and the burned-in match clock.
The tracking overlay uses `video_frame = frame_id − 1`, the same index as the labels, so it always
shows the tracking of the frame the labels are for.

## How it was checked

Against desktop Chrome, with the CSVs parsed separately from the player: 116 seeks (edges, every
ground-truth transition ±1, half boundaries, random frames), 240 button steps across the
transitions, held-key stepping, 4 s of playback, and the next-change buttons. Every label matched
its CSV row and every seek landed on the requested frame. The decoded picture at 10 frames was
compared with ffmpeg's decode of the same frame and was closest to frame *n*, not *n*±1.

Tracking overlay: for 25 frames, projected player positions were sampled on the shirt and split
into two colour groups; the groups matched the Barcelona / Athletic slots 88% of the time, against
about 55% (chance) when the tracking came from 3 s earlier or later.

## Privacy

The server listens on `127.0.0.1` only and rejects requests whose `Host` is not a loopback name.
The video is streamed from `data\` as it is, never copied. `player\` contains no match data and is
safe to commit; `data\` and `evaluation\results\*.csv` stay ignored by git as before.
