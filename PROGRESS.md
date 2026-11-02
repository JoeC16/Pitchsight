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

## Phase C: real detection — IN PROGRESS: everything that doesn't need footage is built

**Still blocked on footage.** A Google Drive link was supplied, but this environment's network policy
refused `drive.usercontent.google.com` (proxy 403). To fix: environment menu in the session's
title bar, then Edit, then Network access, then allow `drive.google.com` and
`drive.usercontent.google.com`, and start a new session. No result below comes from real video.

**Ground truth is no longer required up front.** Agreed route: the coach verifies detections in the
review queue, tags misses, and marks stretches as fully checked; those become ground truth.
A hand-tagged CSV still works.

| Piece | Status | Verified by |
|---|---|---|
| Detection runs saved unedited (`DetectionRun`) | built | tests; scoring after edits uses the original output |
| Checked stretches (`VerifiedSpan`), refused while unreviewed events inside | built | API tests; browser test |
| Evaluation: precision / recall / team / attributes per type; API, CLI (`python -m app.evaluate`, also `--truth-csv`), UI panel | built | exact counts from a scripted review; CLI = API; demo runs labelled |
| Pitch calibration: pack-driven geometry, homography from clicked landmarks, overlay, zone/side from position | built | synthetic camera recovers unclicked points to <0.5 m with 1.5 px click noise; browser test calibrates a synthetic pitch video by clicking (0.55 m RMS, unclicked landmark 0.73 m off) |
| Possession timeline from tracking | built | synthetic scenarios (flicker ignored, passes kept) |
| Rule-based kickouts (length / target / retained-lost / clean-break), shots (zone / side / source; outcome only when clearly wide), turnovers (zone / kind) with confidence | built | 15 synthetic scenario tests incl. direction swap at half time, pass vs shot, kickout not double-counted as turnover |
| Kit-colour team assignment (relative: nearer kit, clearly; keepers/refs unassigned) | built, wired | kit colours + 2nd-half time now in pitch setup |
| `pipeline/real.py` behind the Detector interface | built; runs from `<video>.tracking.json` | app-level test: upload, "set up the pitch" failure, calibrate, reprocess, events stored as `ai`, evaluable |
| Frame decoding + detection + tracking + teams -> Tracking (`video_pipeline.py`) | built | synthetic rendered matches: video -> events == rules on true positions, also through the app |
| RF-DETR (Apache-2.0, COCO weights) as `PITCHSIGHT_FRAME_DETECTOR=rfdetr` | wired, runs | installs from PyPI, weights download OK, ~0.5 s/frame on CPU. **Accuracy on Veo footage unknown** (COCO weights; the ball is tiny) until footage |
| Outcome classifier (goal/point/saved) | **not built** | needs labelled footage |
| `docs/LICENCES.md` | done | from installed package metadata; flags GPL ffmpeg build |

**Fixed along the way:** reprocess re-added detections next to kept reviewed/manual events (duplicates).

**Open questions for you**

- GAA zones: the pack lists `inside_45` *and* `attacking_third`. On a 140 m pitch the 45 m line is
  at 32% of the length, so nearly all of the attacking third is inside the 45. The rules check
  `inside_45` first, so `attacking_third` almost never fires. Should zones be thirds only, or
  `inside_45` plus a band such as "45 to 65 m"?
- Shot outcome from a single camera: a ball in the air projects to the wrong ground position, so
  point vs goal vs saved can't come from tracking alone. Plan: leave it for review, then train a
  classifier on verified shots.
- Frees aren't detected (need the referee's whistle; possibly audio later).

## Preparing for the 100 tagged games (arriving mid-week)

| Piece | Status | Verified by |
|---|---|---|
| Tag importers: Sportscode/Nacsport-style XML, CSV (delimiter + time format detected) | built | fixtures for both; `python -m app.tags inspect` lists codes/labels and writes a starter mapping |
| Provider mapping YAML -> sport-pack events (team by name, "tag names the loser" inversion, value maps, ignore list), all validated; report of unmapped codes/values, missing teams, invalid | built | tests; `convert --strict` fails on anything unmapped |
| Dataset manifest + `python -m app.dataset import / split / run / evaluate` | built | grouped split never puts a ground in two splits; end-to-end import -> run -> evaluate on generated games |
| `python -m app.dataset load`: games into the app as browsable matches (imported human tags, origin `imported`), re-runnable, skips games whose tags run past their video | built | tests incl. bucket storage and a too-short video |
| Needs from the provider | **waiting** | one sample game + tag file; whether times are video- or match-clock; their definitions; written permission to train a commercial product on it |

## Phase D: production readiness — IN PROGRESS (started early, with your go-ahead)

| Piece | Status | Verified by |
|---|---|---|
| Postgres | works | whole suite passes on Postgres 16 locally and in CI; migrations == models checked on Postgres |
| Docker image (python slim + ffmpeg, non-root, healthcheck, `/api/health`) | built | CI builds and starts it (couldn't build here: this sandbox's containers can't reach its network proxy) |
| docker-compose: app + worker + Postgres + S3 emulator | built | CI job uploads a video and waits for the worker to process it via the bucket |
| CI (GitHub Actions): pytest on SQLite and Postgres, both browser tests, docker stack | green (run 6) | runs 2-5 failed and were fixed: unpullable MinIO images (replaced by an S3 emulator from our own Dockerfile), a dev-stage path bug, duplicate parallel image builds |
| Object storage (`PITCHSIGHT_STORAGE=s3`: S3 / R2 / MinIO), local disk as cache, signed-URL playback/downloads | built | moto tests (upload, playback redirect, clips from a machine without the file, reels, deletion); local run with API and worker on separate disks, API copy deleted |
| Persistent job queue (`PITCHSIGHT_JOBS=queue`) + `python -m app.worker` (atomic claim, heartbeat, retry, give up after 3, kinds per worker) | built | tests incl. dead worker retried then failed, heartbeat keeps a long job alive, proxy/reel jobs on a separate worker |
| GPU worker on a provider (Modal/RunPod) | not built | needs an account and the detection model |
| Direct browser -> bucket uploads (multipart, signed part URLs, 4 parallel, retries; PUT fallback) | built | moto tests; full browser test against an S3 emulator: 11 parts, no bytes through the API, identical size |
| Security: sign-in rate limits, invites, password reset/change, CSRF header, CSP + headers, member removal, club export/delete | built | 17 tests; mutation-tested (CSRF check, session cascade); browser test covers invite + reset-by-link |
| Hosting, backups, monitoring, GDPR paperwork, billing | not started | needs your decisions/accounts |

**Fixed along the way:** a worker on another machine would have tried to download into the API
machine's directory (cache path now per machine).
