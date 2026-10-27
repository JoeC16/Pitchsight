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
