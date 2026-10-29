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

## 3. Ground truth: optional

You don't need to tag anything in advance. Once detection runs on a match:

1. Watch a stretch (ideally a whole half) **before** opening the review queue, so the detections
   don't steer you.
2. In the review queue confirm (Enter), fix (E) or delete (D D) every event in that stretch, and
   **+ Tag** anything it missed.
3. Under **Checked stretches**, mark that stretch as checked. The app refuses while any event in it
   is unreviewed.

Precision and recall then appear in the app (or `python -m app.evaluate --match ID`).

If anyone does tag matches separately, use `ground_truth_template.csv` (columns from the sport
pack): `t_start` in **seconds into the video file**, `team` per the pack's `team_means` (turnover =
team that **won** the ball, free = team that **conceded**), and attribute values exactly as in the
pack. Then run `python -m app.evaluate --match ID --truth-csv file.csv`.

## 4. Getting the files here

The dev environment is a temporary cloud container, so files can't be committed to git. Put them
somewhere downloadable (Dropbox or Google Drive share link, or an S3/GCS signed URL) and send the
links. The environment's network policy must allow the host: Google Drive needs
`drive.google.com` and `drive.usercontent.google.com` under Network access in the environment
settings (it was blocked on the first try).
