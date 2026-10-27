"""Runtime configuration, read from environment variables once at import time."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = Path(os.environ.get("PITCHSIGHT_DATA_DIR", ROOT / "data"))
VIDEO_DIR = DATA_DIR / "videos"
CLIP_DIR = DATA_DIR / "clips"
DB_URL = os.environ.get("PITCHSIGHT_DB_URL", f"sqlite:///{DATA_DIR / 'pitchsight.db'}")
SPORT_PACK_DIR = Path(os.environ.get("PITCHSIGHT_SPORT_PACKS", ROOT / "sport_packs"))
FRONTEND_DIR = ROOT / "frontend"

# Veo match files are several GB; keep the cap generous but finite.
MAX_UPLOAD_BYTES = int(float(os.environ.get("PITCHSIGHT_MAX_UPLOAD_GB", "20")) * 1024**3)
ALLOWED_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv"}

# Seconds of lead-in / follow-through added around an event when cutting a clip.
CLIP_PRE_S = float(os.environ.get("PITCHSIGHT_CLIP_PRE_S", "5"))
CLIP_POST_S = float(os.environ.get("PITCHSIGHT_CLIP_POST_S", "3"))

# Which detector turns video into events. "stub" generates synthetic demo events.
DETECTOR = os.environ.get("PITCHSIGHT_DETECTOR", "stub")
