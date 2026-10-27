"""Runtime configuration, read from environment variables once at import time."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = Path(os.environ.get("PITCHSIGHT_DATA_DIR", ROOT / "data"))
VIDEO_DIR = DATA_DIR / "videos"
CLIP_DIR = DATA_DIR / "clips"
REEL_DIR = DATA_DIR / "reels"
PROXY_DIR = DATA_DIR / "proxies"
DB_URL = os.environ.get("PITCHSIGHT_DB_URL", f"sqlite:///{DATA_DIR / 'pitchsight.db'}")
SPORT_PACK_DIR = Path(os.environ.get("PITCHSIGHT_SPORT_PACKS", ROOT / "sport_packs"))
FRONTEND_DIR = ROOT / "frontend"

# Veo match files are several GB; keep the cap generous but finite.
MAX_UPLOAD_BYTES = int(float(os.environ.get("PITCHSIGHT_MAX_UPLOAD_GB", "20")) * 1024**3)
ALLOWED_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv"}

# Seconds of lead-in / follow-through added around an event when cutting a clip.
CLIP_PRE_S = float(os.environ.get("PITCHSIGHT_CLIP_PRE_S", "5"))
CLIP_POST_S = float(os.environ.get("PITCHSIGHT_CLIP_POST_S", "3"))
# Upper bound on events in one exported reel (keeps a single job's runtime bounded).
MAX_REEL_EVENTS = int(os.environ.get("PITCHSIGHT_MAX_REEL_EVENTS", "300"))

# Which detector turns video into events. "stub" generates synthetic demo events.
DETECTOR = os.environ.get("PITCHSIGHT_DETECTOR", "stub")

# Auth. Signup creates a new, empty club, so leaving it open doesn't expose other clubs' data.
ALLOW_SIGNUP = os.environ.get("PITCHSIGHT_ALLOW_SIGNUP", "true").lower() == "true"
SESSION_DAYS = float(os.environ.get("PITCHSIGHT_SESSION_DAYS", "30"))
# Set true when served over HTTPS so the session cookie is never sent in clear text.
SECURE_COOKIES = os.environ.get("PITCHSIGHT_SECURE_COOKIES", "false").lower() == "true"

# Browser playback proxy: "auto" transcodes only when the original won't play smoothly
# (see proxy.needs_proxy), "always" or "never" force it.
PROXY_MODE = os.environ.get("PITCHSIGHT_PROXY", "auto")
