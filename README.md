# PitchSight

Match analysis for GAA and soccer clubs. Download a game from Veo, upload it, then filter
the match to the moments that matter (turnovers, kickouts, shots, scores) and replay them as a reel.

> **Status: working scaffold.** Upload, storage, taxonomy, filtering, clips, review/tagging and
> the UI are built. Detection is **simulated** (a stub emits synthetic events) until the real
> computer-vision pipeline is built. See `docs/ROADMAP.md`.

## Quick start

Requires Python 3.11+ and ffmpeg.

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest
uvicorn app.main:app --reload
```

Open http://localhost:8000, click **New match**, pick a Veo mp4 and upload. Events appear when
processing finishes (seconds with the stub).

## Workflow

1. In Veo, download the match recording.
2. Upload it in PitchSight with the team names and sport.
3. Filter by event type, team, half, attributes (outcome, zone, source...) and AI confidence.
4. Play the filtered reel or export it as one mp4, download single clips, correct wrong events, or tag moments by hand.
5. Press **Review** to step through unreviewed events, least confident first (Enter / E / D D / arrows / Esc).

## Adding a classification or a sport

Edit `sport_packs/*.yaml`. The API validates against it and the UI builds its filters from it; no
code changes needed. Copy `soccer.yaml` to start a new sport.

## Config (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `PITCHSIGHT_DATA_DIR` | `./data` | sqlite db, videos, cached clips |
| `PITCHSIGHT_DETECTOR` | `stub` | `stub` or `real` |
| `PITCHSIGHT_MAX_UPLOAD_GB` | `20` | upload size cap |
| `PITCHSIGHT_CLIP_PRE_S` / `_POST_S` | `5` / `3` | clip padding in seconds |
| `PITCHSIGHT_MAX_REEL_EVENTS` | `300` | most events allowed in one exported reel |
| `PITCHSIGHT_PROXY` | `auto` | browser playback proxy: `auto` (only when needed), `always`, `never` |
| `PITCHSIGHT_DB_URL` | sqlite in data dir | any SQLAlchemy URL |
| `PITCHSIGHT_ALLOW_SIGNUP` | `true` | allow `POST /api/auth/register` (creates a new club + admin) |
| `PITCHSIGHT_SESSION_DAYS` | `30` | login session lifetime |
| `PITCHSIGHT_SECURE_COOKIES` | `false` | set `true` behind HTTPS |

## Accounts

Each club's users see only that club's matches. The first person signs up in the UI ("Create an
account"), which creates the club and makes them its admin; admins add coaches from the club
menu in the top bar. From the command line (e.g. with sign-up closed):

```bash
python -m app.cli create-user coach@club.ie --club "St Mary's GAC"   # prompts for password
```

The database schema is managed by Alembic and upgraded automatically on startup. After changing
`models.py`, run `alembic revision --autogenerate -m "..."` in `backend/` and review the result.

## Docs

`CLAUDE.md` (for Claude Code), `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`.
