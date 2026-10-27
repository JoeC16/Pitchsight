# PitchSight: guide for Claude Code

Working name for a match-analysis product sold to GAA and soccer clubs. A club downloads a
game from Veo, uploads it here, and the app finds the key moments (turnovers, shots,
kickouts...), lets coaches filter to specific clips, and shows patterns of play.

## Status

See `PROGRESS.md` for what is done, verified and blocked. The test suite runs and passes, and
`e2e/ui_smoke.js` drives the whole UI in headless Chromium against a running server:

```bash
node e2e/ui_smoke.js path/to/video.mp4 http://localhost:8000 /tmp/shots
```

## Run it

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # needs ffmpeg + ffprobe on PATH
pytest
uvicorn app.main:app --reload             # UI + API at http://localhost:8000
```

Data (sqlite db, videos, cached clips) lives in `./data` (git-ignored). Config is env vars,
see `backend/app/config.py` (`PITCHSIGHT_DATA_DIR`, `PITCHSIGHT_DETECTOR`, `PITCHSIGHT_MAX_UPLOAD_GB`, ...).

## Layout

```
sport_packs/*.yaml        taxonomy per sport: event types + attributes + scoring. SOURCE OF TRUTH.
backend/app/
  main.py                 app, lifespan (create tables, fail stuck jobs), static frontend mount
  models.py               Match, Event (attributes is a JSON dict)
  taxonomy.py             loads packs, validate_event()
  queries.py              ?type=&team=&period=&min_conf=&attr.<id>= filtering
  analytics.py            generic summary: counts, breakdowns, score (driven by pack `scoring`)
  clips.py                ffmpeg clip cutting with on-disk cache
  routers/                sports, matches (upload/video/events/summary), events (patch/delete/clip)
  pipeline/
    interfaces.py         Detector protocol, EventDraft, VideoInfo   <- the key seam
    stub.py               SYNTHETIC events for development (origin="stub")
    real.py               skeleton for the real CV pipeline (not implemented)
    runner.py             process_match(): probe -> detect -> validate -> store
    probe.py              ffprobe wrapper
backend/tests/            pytest + TestClient; test_api.py covers the whole API
frontend/                 plain HTML/CSS/JS, no build step (index.html, app.js, style.css)
docs/ARCHITECTURE.md      pipeline stages, data model, API
docs/ROADMAP.md           phased plan with exit criteria and risks
```

## Design decisions to preserve

- **Sport packs drive everything.** Adding a classification = editing YAML. The API validates
  against it and the UI builds its filter chips from it. Don't hard-code event types or values
  anywhere else. A new sport = a new YAML file.
- **Every filter is a query.** UI filter state maps 1:1 to query params on
  `GET /api/matches/{id}/events`. Keep it that way so reports and exports reuse it.
- **`Event.team` semantics differ per event type** and are documented in each pack's
  `team_means` (shot = shooting team, turnover = team that WON the ball, kickout = kicking team,
  free = team that conceded). Keep this consistent when writing real detection.
- **`origin` and `reviewed` model trust.** `origin` is `stub | ai | manual`. Any human edit sets
  `reviewed=true`. `reprocess` replaces only unreviewed non-manual events, so coaches' corrections
  survive a model upgrade. Confidence is stored so low-confidence items can be queued for review.
- **The Detector protocol is the seam** between video and everything else. The runner validates
  every draft against the pack and drops invalid ones, so a buggy model can't corrupt data.
- **Upload is a raw streamed PUT**, not multipart, so multi-GB Veo files go straight to disk.
- **Demo data is labelled.** The UI shows a banner whenever a match's events came from the stub.
  Never let simulated events look like real analysis, especially in anything shown to a customer.

## Not built yet (don't assume these exist)

Real detection (everything in `pipeline/real.py`), auth and multi-tenancy (clubs/teams/users),
video proxy transcoding for browser playback, reel export (concatenated mp4), a job queue and GPU
worker (processing runs in-process via FastAPI BackgroundTasks), Postgres and object storage
(sqlite + local disk), migrations (tables are created with `create_all`), Veo import automation
(users download the file from Veo and upload it by hand).

## Suggested next tasks, in order

1. Run tests, fix breakage, click through the UI.
2. Get 2-3 real Veo GAA match files and check codec/resolution/size; add a transcoded proxy if
   browsers can't play them smoothly.
3. Pitch calibration on real footage (fixed panoramic camera -> one homography per match).
4. Player/ball detection + tracking + team clustering -> possession timeline.
5. Rule-based events from tracking (turnover, kickout, shot), emit confidence, validate by hand.
6. Review queue UI: step through low-confidence events, confirm or fix with one key.
7. Label a GAA dataset (scoring outcomes, kickouts) and train classifiers where rules fall short.
8. Postgres + S3-style storage + queue/GPU worker; auth with per-club isolation.
9. Reel export, pattern reports (kickout maps, shot maps, turnover heatmaps).

Check the licences of any open-source model/dataset before using it commercially.

## Conventions

Python 3.11+, type hints, small modules, sync routes unless streaming. Tests for every API
behaviour. Keep the frontend dependency-free until there's a reason to introduce a build step.
