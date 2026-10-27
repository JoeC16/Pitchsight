# Sample matches (needed for Phase C)

Real detection can't be built or measured without real footage. Nothing in this folder except this
README and the CSV template is committed: video is large and shows identifiable people.

## 1. Footage

| | What | Why |
|---|---|---|
| **How many** | 3 full GAA football matches (2 to build against, 1 held out for evaluation). Soccer optional. | One match overfits; the held-out one tells us if it generalises. |
| **Which Veo export** | The **full panoramic (whole-pitch) view** if your Veo plan lets you download it, *and* the standard AI-followed match video. If you can only get one, say which. | The plan is one homography per match, which only works if the camera doesn't pan or zoom. The followed view pans and zooms, so it would need per-frame calibration (much harder). I don't know exactly which exports your Veo plan offers, so please tell me what you see. |
| **Format** | The file exactly as downloaded: no trimming, re-encoding or screen recording. Full 2 x 30/35 min halves plus breaks is fine. | Codec, resolution and keyframe spacing are what's being checked; re-encoding hides them. |
| **Variety** | Different grounds if possible; at least one in poor light or rain; kits that are clearly different colours (one match with similar kits is useful later). | Team assignment is kit-colour clustering; lighting is the usual failure. |
| **Rights** | Only matches your club owns or has permission to use. Ideally adult matches to start with. | The footage identifies players, possibly minors (GDPR, consent). |

## 2. Per-match details (a short text note per file)

- Home and away team names, **jersey + shorts colours for each team and each goalkeeper**.
- Which end each team attacks in the first half (e.g. "home attack the clubhouse end").
- Kick-off time in the video for each half (e.g. "1st half starts 00:02:10, 2nd 00:41:30").
- Pitch size if known (GAA pitches vary: 130-145 m x 80-90 m).

## 3. Hand-tagged ground truth (CSV)

Use `ground_truth_template.csv` (columns are generated from `sport_packs/gaa_football.yaml`).
Delete the example rows. Tag **every shot, kickout and turnover** in at least the held-out match;
one half of each other match is enough to start.

- `t_start` / `t_end`: **seconds into the video file**, not the match clock (e.g. 62.0 = 1:02 in the file).
  `t_start` is the moment it happens (kick struck, ball won). `t_end` can be a rough guess.
- `team`: follow `team_means` in the sport pack. **shot** = team shooting, **turnover** = team that
  **won** the ball, **kickout** = team kicking it out, **free** = team that **conceded** it.
- Attribute columns: use the exact values from the sport pack (e.g. `point`, `two_pointer`, `wide`),
  blank if unsure. Only `video_file`, `type`, `team`, `t_start` are required.
- The evaluation will count a detection as correct when the type and team match and `t_start` is
  within a tolerance (default 3 s; adjustable).

## 4. Getting the files here

The dev environment is a temporary cloud container, so files can't be committed to git. Put them
somewhere downloadable (Google Drive/Dropbox share link, or an S3/GCS signed URL) and send the
links; I'll download them into this folder. If the container's network policy blocks that host,
I'll say so and point you at the setting.
