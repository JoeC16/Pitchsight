# Architecture

## Flow

```
Veo (manual download) -> browser PUT /api/matches/{id}/video (streamed to disk)
        |
        v
  background job: process_match
     probe (ffprobe) -> Detector.detect() -> validate each draft vs sport pack -> store Events
        |
        v
  SQL (matches, events)  <-- filter queries --  UI (timeline, chips, reel, editor)
        |
        v
  ffmpeg clip cutter (cached) -> per-event mp4 download
```

## Real detection pipeline (target; `pipeline/real.py`)

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

Hard parts: ball outcome (point vs wide vs saved) from a distant view; GAA-specific training data
barely exists publicly, so a labelled dataset is part of the work.

## Data model

- `Match`: title, sport, teams, status (`created -> uploaded -> processing -> ready | failed`),
  progress, duration/fps, video path, which detector produced its events.
- `Event`: match, type, team, period, `t_start`/`t_end` (seconds), `attributes` (JSON, validated
  against the pack), `confidence` (null for manual), `origin` (`stub|ai|manual`), `reviewed`.
- Sport pack (YAML, not in the db): event types, their attributes and allowed values, scoring rules.

## API (all under `/api`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/sports`, `/sports/{sport}` | list packs; full taxonomy for the UI |
| POST/GET | `/matches` | create (metadata only) / list |
| GET/DELETE | `/matches/{id}` | fetch / delete incl. files |
| PUT | `/matches/{id}/video` | raw body upload, `X-Filename` header; triggers processing |
| GET | `/matches/{id}/video` | stream source video (Range supported) |
| POST | `/matches/{id}/reprocess` | re-run detection, keeping manual/reviewed events |
| GET | `/matches/{id}/events` | filtered list (see below) |
| POST | `/matches/{id}/events` | manual tag (validated) |
| GET | `/matches/{id}/summary` | counts, per-attribute breakdowns, score |
| GET/PATCH/DELETE | `/events/{id}` | read / correct (sets reviewed) / remove |
| GET | `/events/{id}/clip` | cut and cache an mp4 around the event |

Event filters: `type=a,b` `team=home|away` `period=1|2` `min_conf=0.6` `attr.<id>=v1,v2`.

## Scaling notes

- Processing is in-process today. Move `process_match` behind a queue and run it on a GPU worker;
  the function is already self-contained.
- Swap sqlite for Postgres (set `PITCHSIGHT_DB_URL`, add Alembic) and local disk for object storage.
- GPU time per match is the main unit cost; price plans accordingly.
