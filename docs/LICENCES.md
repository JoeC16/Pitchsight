# Licences of third-party code, models and data

Checked from installed package metadata (versions as installed in the dev environment). Not legal
advice: have a solicitor review this before selling the product.

## In use

| Component | Version | Licence | Commercial use | Notes |
|---|---|---|---|---|
| FastAPI | 0.162 | MIT | yes | |
| Starlette | 1.10 | BSD-3-Clause | yes | |
| Uvicorn | 0.54 | BSD-3-Clause | yes | |
| SQLAlchemy | 2.1 | MIT | yes | |
| Alembic (+ Mako) | 1.20 | MIT | yes | |
| Pydantic (+ core) | 2.14 | MIT | yes | |
| PyYAML | 6.0 | MIT | yes | |
| NumPy | 2.5 | BSD-3-Clause (bundled parts: 0BSD, MIT, Zlib, ...) | yes | |
| httpx, pytest | dev/test only | BSD-3-Clause, MIT | yes | not shipped |
| Playwright | dev/test only | Apache-2.0 | yes | not shipped |
| **ffmpeg** (system binary) | 6.1 | **GPL** in this build (`--enable-gpl --enable-libx264`) | see note | |

**ffmpeg note.** PitchSight runs ffmpeg as a separate program and does not link to it, so the GPL
does not extend to PitchSight's own code. If you *distribute* an ffmpeg binary built with x264
(e.g. inside a Docker image you give to customers), you take on the GPL's obligations for that
binary (offer its source). Running it on your own servers is not distribution. An LGPL-only
ffmpeg build without x264 avoids this but loses the H.264 encoder used for clips, reels and
proxies; a commercial x264 licence also exists.

## Candidates for detection (not yet adopted)

To be confirmed, including the licence of the exact **weights** file, when adopted. Model code and
weights can carry different licences.

| Component | Licence (code) | Commercial use | Notes |
|---|---|---|---|
| RF-DETR (Roboflow) | Apache-2.0 | yes | preferred detector; confirm weights licence per checkpoint |
| ByteTrack | MIT | yes | tracker |
| supervision (Roboflow) | MIT | yes | tracking/annotation utilities |
| Ultralytics YOLO | AGPL-3.0 | **only with their enterprise licence** (or open-sourcing the service) | avoid unless clearly better |
| SoccerNet datasets | NDA, research use | **no** | don't train commercial models on it |

## Footage

Match footage belongs to whoever recorded it; Veo's terms of service apply to Veo recordings.
Only use footage the club owns or has permission to use, and plan consent/GDPR handling,
especially for underage players. Broadcast footage (TV, YouTube) is not usable without the
rights holder's permission.
