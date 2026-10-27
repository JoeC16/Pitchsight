# Progress

Running log of what's done, what's verified (and how), and what's blocked.

## Phase A: make it work — DONE

**Repo layout restored.** The repo had the scaffold uploaded flat (13 files at the root) and was
missing every Python module under `backend/app/`. The full tree was in `pitchsight.zip`
(commit a981d3b, deleted in dada1de). The flat files were byte-identical to the zip's copies,
so they were moved into place with `git mv` and the missing files were restored from the zip.

**Environment:** Python 3.13.17, ffmpeg 6.1.1. Resolved versions: fastapi 0.162.0,
starlette 1.10.0, SQLAlchemy 2.1.6, pydantic 2.14.1, uvicorn 0.54.0, httpx 0.28.1, pytest 9.2.0.

**Verified**

- `pytest`: 13/13 pass on first run, including the real-ffmpeg end-to-end test (not skipped).
- Live server, real HTTP (curl) against a generated 180 s 1280x720 h264/aac test video:
  create match (201), streamed PUT upload of 55 MB (200, 0.3 s), status polled
  `processing -> ready`, ffprobe got duration 180 s / 25 fps, 145 stub events, filters
  (`type`, `team`, `period`, `min_conf`, `attr.outcome`) return only matching rows, PATCH sets
  `reviewed=true`, clip download is a valid 12.2 s h264/aac mp4, Range request returns 206,
  summary score agrees with the shot events (1 point + 2 two-pointers = 0-05 (5)).
- Headless Chromium (`e2e/ui_smoke.js`), 23 checks: upload via dialog, processing poll, demo
  banner, timeline markers + dimming, type/attribute/team filters + reset, timeline click seek,
  reel play + auto-advance + stop, edit (team change -> reviewed), manual tag at playhead,
  invalid edit shows error, clip download, no console errors. Chromium reports
  `canPlayType(h264/aac) = "probably"`.

**Bugs fixed**

- Sidebar rendered the literal text `null` under the match list (`replaceChildren` with a null
  child). Same latent bug in the scoreline for packs without `scoring`.
- Clip download button (`<a class="btn">`) used default link colour.

**Known, not fixed (UX, not bugs)**

- With several attribute groups open, the filter panel scrolls; the confidence slider and
  Reset sit below the fold.
- Test video is synthetic (`testsrc2`). Real Veo files have not been tried (see Phase C).

## Phase B: harden the product — DONE

Exit check: `pytest` 64/64 pass; `e2e/ui_smoke.js` 48/48 browser checks pass (run with both a
browser-friendly mp4 and an mpeg4 .mkv to exercise the proxy).

| Feature | What was built | Verified by |
|---|---|---|
| Alembic | `backend/migrations/`, run on startup by `app/migrate.py`; pre-Alembic databases are stamped at `0001` and kept | `test_migrations.py` (models == migrations, down/up round-trip, legacy adoption); the Phase A dev DB was adopted with its 4 matches |
| Auth + isolation | `Club`, `User` (admin/member), `AuthSession` (hashed token, cookie or bearer), scrypt passwords, `python -m app.cli create-user`. All match/event/reel routes scoped; other clubs get 404 | `test_auth.py`: 15 routes need login; every per-match/event route is 404 cross-club and changes nothing; mutation test (drop the club check) fails the suite. Browser: register, sign out (API 401), sign in, add coach |
| Review queue | `?reviewed=false&order=confidence` on the normal events query (so filters apply); review card plays each event; Enter / E / D twice / arrows / Esc | API tests for ordering and filter combination; browser walks every key incl. "keys ignored while the editor is open" |
| Reel export | `POST /matches/{id}/reels?<filters>` -> job: merge overlapping windows, cut via clip cache, concat demuxer `-c copy`; status, download, delete | Real-ffmpeg test checks segment count and duration vs. expected; live 30-event reel decodes with no errors and A/V durations match (154.52 s); browser exports and downloads |
| Playback proxy | `needs_proxy()` rules (container, codec, pix_fmt, audio, >1080p, >20 Mbit/s, keyframe gap >10 s), built after detection with progress, served by `/video` once ready | Unit tests for each rule; real-ffmpeg mkv/mpeg4 upload gets an h264 proxy, friendly mp4 served byte-identical, failure falls back. Browser: banner, then 1280px playback; before the proxy Chromium shows a black audio-only player for the mkv |

**Also fixed in Phase B**

- Matches stuck in `uploaded` (queued job lost on restart) were never failed; only `processing` was.
- Proxy races: reprocess while the proxy builds is refused; re-uploading drops a stale proxy.
- Downloaded clips/reels of stub events carry `DEMO-simulated-event(s)` in the filename: the demo
  banner doesn't travel with a file.

**Known gaps (not blocking)**

- No login rate limiting / lockout, no password reset, no email verification. Signup is open by
  default (`PITCHSIGHT_ALLOW_SIGNUP=false` to close). CSRF relies on `SameSite=Lax` cookies.
- Jobs (detection, proxy, reels) still run in-process; a restart fails them (marked so in the UI).
  The queue/worker is Phase D.
- SQLite foreign keys aren't enforced; deletes cascade through the ORM instead.

## Phase C: real detection — BLOCKED: no footage

`sample_matches/` did not exist (no footage supplied). As instructed, nothing in Phase C has been
started and no detection results have been produced or simulated. `PITCHSIGHT_DETECTOR=real`
still raises "not built yet", and stub results still carry the Demo data banner.

**Needed to unblock** (details in `sample_matches/README.md`):

1. 3 full GAA matches as downloaded from Veo, ideally the full panoramic (fixed, whole-pitch)
   export. The planned single-homography calibration doesn't work on the panning/zooming
   followed view. Which exports the Veo plan offers is unknown: needs confirming.
2. Per-match notes: kit and goalkeeper colours, which end each team attacks first, half start times.
3. Hand-tagged ground truth in `sample_matches/ground_truth_template.csv` format (generated from
   the sport pack): every shot, kickout and turnover in at least the held-out match.
4. Download links (files can't go in git; the container is temporary).

**Early licence note for the model choices** (to confirm and record in `docs/LICENCES.md` in
Phase C): Ultralytics YOLO is AGPL-3.0 (commercial use needs their enterprise licence or opening
the source); RF-DETR is Apache-2.0; ByteTrack is MIT. SoccerNet data needs an NDA and is research-only.

## Phase D: production readiness — not started (waiting on Phase C, per the phase order)
