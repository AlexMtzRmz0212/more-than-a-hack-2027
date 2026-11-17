# Possession has a shape — Team TikiTako (solo entry, Alex Martinez)

**Presentation:** `presentation/index.html` in this repository (open it in a browser; press Space to move down).

## Honest status

I entered alone after six people I asked to join could not commit, and I learned of the hackathon
late. I did not finish a model. **The possession file I am submitting is the provided starter
baseline, `baseline_smooth50`, unchanged** (`starter/baseline.py`, nearest player + ±2 s majority
vote). I claim no score as my own work. What I built is described below.

## Approach (proposed)

A ball in possession moves differently from a ball nobody controls, and the players around it
arrange themselves differently. The method I would build:

1. **Per-frame features** — ball speed, acceleration, curvature; distance to the nearest player of
   each team; time the ball stays within reach of one player; support geometry (teammates at
   8–15 m at distinct angles) and pressure (opponents converging on the ball); team width/depth.
2. **Gap filling** — short ball gaps bridged with rolling-deceleration and flight models; long gaps
   decided by player geometry, which does not need the ball.
3. **State model** — hidden states *dead / controlled-by-team / loose*, with minimum durations and
   hysteresis (HMM / Viterbi). Loose keeps the last team, per the challenge rules.
4. **Labels** — a hand-over only when a new team controls the ball long enough; otherwise no change.
   This directly targets spellF1, which punishes invented possessions.

## Assets used

- **Tracking (ATD, tactical)** and its metadata — for the exploration below and the review player.
- **Homography** — only in the review player, to draw the tracking on the video.
- **Smart Tagging, broadcast video** — not used.

### Exploration on the public 5 minutes (description only, not training or tuning)

`presentation/make_glimpse.py` → `presentation/glimpse.json` (aggregates only):

| state | median ball speed | ball < 2 m/s | nearest opponent to the ball's nearest player |
|---|---|---|---|
| H (Barcelona) | 5.3 m/s | 16% | 7.5 m |
| A (Athletic) | 6.6 m/s | 18% | 5.4 m |
| D (dead) | 2.7 m/s | 39% | 16.9 m |

The nearest player is the wrong team on 22% of live, ball-tracked frames.

## Training data

None. No labelled data was used.

## Possession rules (proposed)

- Dead ball: tracking says dead, or ball slow and unpressed for a sustained period.
- Loose balls, deflections, aerial duels without control: possession stays with the last team.
- Change of possession: the other team keeps the ball within reach for a minimum time, or completes a pass.

## Limitations

No model was implemented in time; the submitted file has the baseline's limitations
(fragmentation, weak dead-ball class, blind where the ball is missing).

## Also built

`player/` — a local, frame-accurate review player: tactical video with ground truth and every
prediction CSV stacked as coloured stripes, plus the tracking projected through the homography.
See `player/README.md`.

## Reproduction

```bash
pip install -r requirements.txt
python starter/baseline.py                       # writes evaluation/results/baseline_smooth50.csv
python evaluation/validate_submission.py evaluation/results/baseline_smooth50.csv
python presentation/make_glimpse.py              # exploration aggregates
python player/serve.py                           # review player
```

Contact: alejandro.martinez.rmz97+fcb@gmail.com · [LinkedIn](https://www.linkedin.com/in/alejandro-mtz) · [Website](https://bittobyte.qzz.io) · [Portfolio](https://alex.bittobyte.qzz.io)
