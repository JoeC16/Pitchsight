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

## Phase B: harden the product — not started

## Phase C: real detection — not started

## Phase D: production readiness — not started
