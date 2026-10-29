# Architecture

## Flow

```
Veo (manual download) -> browser PUT /api/matches/{id}/video (streamed to disk)
        |
        v
  background job: process_match
     probe (ffprobe) -> Detector.detect() -> validate each draft vs sport pack -> store Events
     -> playback proxy if needed (h264/aac faststart mp4, <=1080p, keyframe every 2s)
        |
        v
  SQL (matches, events)  <-- filter queries --  UI (timeline, chips, reel, editor)
        |
        v
  ffmpeg clip cutter (cached) -> per-event mp4 download
        |
        v
  reel export job: merge overlapping windows -> cut each (clip cache) -> concat (-c copy)
```

**Playback proxy.** `proxy.needs_proxy()` transcodes only when the original won't play smoothly
in a browser: container other than mp4/mov, codec other than h264 8-bit 4:2:0, unusual audio,
larger than 1080p, over 20 Mbit/s, or keyframes more than 10 s apart (slow seeking). The reason is
stored on the match. `GET /matches/{id}/video` serves the proxy once ready, else the original.
Clips and reels always cut from the original.

Downloads of simulated (stub) content carry `DEMO-simulated-event(s)` in the filename, because
the UI's demo banner doesn't travel with the file.

## Real detection pipeline (`pipeline/real.py`; stages 2, 5, 6, 7 built, 1, 3, 4, 8 not)

1. **Decode** frames at a reduced rate.
2. **Calibrate** the pitch. Veo uses a fixed panoramic camera, so one homography per match maps
   pixels to pitch coordinates. GAA markings: goals, 13m, 20m, 45m, 65m lines, halfway.
3. **Detect** players, referees, ball. The ball is tiny in panoramic footage, so expect to fine-tune.
4. **Track** with multi-object tracking for stable ids.
5. **Teams**: cluster kit colours to assign home/away.
6. **Possession** from ball-to-player proximity and ball motion.
7. **Events**: rules over tracking (turnover = possession change, kickout = restart from the
   goal area, shot = ball launched at goal) plus an action model for what tracking can't see.
8. **Classify** attributes (outcome, zone, side, method). Emit a confidence; low confidence goes to review.

Built so far: calibration (`pitch.py`), team assignment (`pipeline/teams.py`, not wired yet),
possession (`pipeline/possession.py`), and rules (`pipeline/rules.py`, GAA thresholds in the pack's
`detection:` section). Until detection and tracking exist, `real.py` reads
`<video>.tracking.json` (format in `pipeline/tracking.py`), and fails with a clear message if
the pitch isn't set up or no tracking exists. Detectors receive a `MatchContext` (calibration,
direction of play).

Hard parts: ball outcome (point vs wide vs saved) from a distant view; GAA-specific training data
barely exists publicly, so a labelled dataset is part of the work.

## Pitch calibration

Sport packs define the pitch (`pitch:` in the YAML: dimensions, lines, boxes, and rules mapping a
position to the pack's `zone`/`side` values). Veo's panoramic camera is fixed, so the coach clicks
4+ landmarks (5+ to get a real error estimate) on one frame in **Set up pitch**; `pitch.py` fits a
homography (normalised DLT) from pixels to metres and stores it, the fit error and the direction
of play on `Match.pitch_setup`. The fitted markings are drawn back over the frame. Untested on real
footage: stitched panoramas may need a lens/cylinder model if one homography fits poorly.

## Measuring the detector (no separate ground truth needed)

Each processing run's validated output is saved as a `DetectionRun` and never edited. A coach
reviews the events (confirm / fix / delete), tags anything missed, then marks a stretch of video
as fully checked (`VerifiedSpan`; refused while unreviewed events remain inside it). Inside
checked stretches the reviewed events are ground truth, so `GET /matches/{id}/evaluation` and
`python -m app.evaluate --match ID` score any run against them: per event type found / missed /
false, precision, recall, team agreement, attribute accuracy (one-to-one matching on type within
±3 s). A hand-tagged CSV (`sample_matches/ground_truth_template.csv`) works too (`--truth-csv`).

## Data model

- `Club`: a customer. `User` belongs to one club (`admin` or `member`). `AuthSession` stores the
  SHA-256 of a random login token (HttpOnly cookie `ps_session`, or `Authorization: Bearer`).
- Every `Match` belongs to a club. All match/event routes resolve the match through the caller's
  club; another club's ids return 404 (not 403) so they can't be probed.
- Schema changes go through Alembic (`backend/migrations/`), run on startup.

- `Match`: title, sport, teams, status (`created -> uploaded -> processing -> ready | failed`),
  progress, duration/fps, video path, which detector produced its events.
- `Event`: match, type, team, period, `t_start`/`t_end` (seconds), `attributes` (JSON, validated
  against the pack), `confidence` (null for manual), `origin` (`stub|ai|manual`), `reviewed`.
- Sport pack (YAML, not in the db): event types, their attributes and allowed values, scoring rules.

## API (all under `/api`; everything except `/sports` and `/auth/{register,login}` needs login)

| Method | Path | Purpose |
|---|---|---|
| POST | `/auth/register` | create club + admin user, sign in (if `PITCHSIGHT_ALLOW_SIGNUP`) |
| POST | `/auth/login`, `/auth/logout` | start / end a session |
| GET | `/auth/me` | current user + club |
| GET/POST | `/auth/users` | list club members / admin adds a member |
| GET | `/sports`, `/sports/{sport}` | list packs; full taxonomy for the UI (no login needed) |
| POST/GET | `/matches` | create (metadata only) / list |
| GET/DELETE | `/matches/{id}` | fetch / delete incl. files |
| PUT | `/matches/{id}/video` | raw body upload, `X-Filename` header; triggers processing |
| GET | `/matches/{id}/video` | stream playback video: proxy if built, else original (Range supported) |
| POST | `/matches/{id}/reprocess` | re-run detection, keeping manual/reviewed events |
| GET | `/matches/{id}/events` | filtered list (see below) |
| POST | `/matches/{id}/events` | manual tag (validated) |
| GET | `/matches/{id}/summary` | counts, per-attribute breakdowns, score |
| GET | `/matches/{id}/frame?t=` | one JPEG frame of the original (for calibration) |
| GET/PUT/DELETE | `/matches/{id}/pitch` | landmarks + markings for the pitch size / save calibration / clear |
| GET | `/matches/{id}/runs` | each detection run's original output (count, detector, time) |
| GET/POST | `/matches/{id}/verified-spans` | stretches marked fully checked (POST refused if unreviewed events inside) |
| DELETE | `/matches/{id}/verified-spans/{span_id}` | unmark a stretch |
| GET | `/matches/{id}/evaluation?run=&tolerance=` | precision/recall of a run vs. checked stretches |
| GET/PATCH/DELETE | `/events/{id}` | read / correct (sets reviewed) / remove |
| GET | `/events/{id}/clip` | cut and cache an mp4 around the event |
| POST | `/matches/{id}/reels?<event filters>` | 202; export matching events as one mp4 (background job) |
| GET | `/matches/{id}/reels` | list a match's exported reels |
| GET/DELETE | `/reels/{id}` | status + progress / delete |
| GET | `/reels/{id}/download` | the mp4, once `status` is `ready` |

Event filters: `type=a,b` `team=home|away` `period=1|2` `min_conf=0.6` `attr.<id>=v1,v2`
`reviewed=true|false` `order=time|confidence`. The review queue is `?reviewed=false&order=confidence`
(least confident first, unknown confidence first) combined with whatever filters are active.

Review UI keys: Enter confirm, E edit, D twice delete, ←/→ prev/next, Esc exit.

## Scaling notes

- Processing is in-process today. Move `process_match` behind a queue and run it on a GPU worker;
  the function is already self-contained.
- Swap sqlite for Postgres (set `PITCHSIGHT_DB_URL`) and local disk for object storage.
- GPU time per match is the main unit cost; price plans accordingly.
