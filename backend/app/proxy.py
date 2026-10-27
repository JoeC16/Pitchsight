"""Browser playback proxy: an h264/aac faststart mp4, made only when the original won't play
smoothly in a browser. Clips and reels are still cut from the original for quality."""
import logging
import re
import shutil
import subprocess
from pathlib import Path

from . import config
from .db import SessionLocal
from .models import Match
from .pipeline.interfaces import VideoInfo

log = logging.getLogger(__name__)

BROWSER_CONTAINERS = {".mp4", ".m4v", ".mov"}
BROWSER_PIX_FMTS = {"yuv420p", "yuvj420p"}
BROWSER_AUDIO = {None, "aac", "mp3"}
MAX_PIXELS = 1920 * 1080
MAX_BIT_RATE = 20_000_000
MAX_KEYFRAME_GAP_S = 10.0


def needs_proxy(info: VideoInfo, path: Path) -> str | None:
    """Why the original needs a proxy, or None if browsers should play it fine."""
    if config.PROXY_MODE == "never":
        return None
    if config.PROXY_MODE == "always":
        return "proxy forced by PITCHSIGHT_PROXY=always"
    if path.suffix.lower() not in BROWSER_CONTAINERS:
        return f"{path.suffix} files don't play in all browsers"
    if info.video_codec != "h264":
        return f"{info.video_codec} video doesn't play in all browsers"
    if info.pix_fmt not in BROWSER_PIX_FMTS:
        return f"{info.pix_fmt} pixel format doesn't play in all browsers"
    if info.audio_codec not in BROWSER_AUDIO:
        return f"{info.audio_codec} audio doesn't play in all browsers"
    if (info.width or 0) * (info.height or 0) > MAX_PIXELS:
        return f"{info.width}x{info.height} is larger than 1080p"
    if info.bit_rate and info.bit_rate > MAX_BIT_RATE:
        return f"bitrate {info.bit_rate / 1e6:.0f} Mbit/s is too high to stream smoothly"
    if info.max_keyframe_gap_s and info.max_keyframe_gap_s > MAX_KEYFRAME_GAP_S:
        return f"keyframes are {info.max_keyframe_gap_s:.0f}s apart, so seeking is slow"
    return None


_OUT_TIME = re.compile(r"^out_time_us=(\d+)$", re.M)


def build_proxy(match_id: int) -> None:
    """Transcode the match video to the proxy. Updates proxy_status/progress as it goes."""
    db = SessionLocal()
    try:
        match = db.get(Match, match_id)
        if match is None or not match.video_path:
            return
        if not shutil.which("ffmpeg"):
            raise RuntimeError("ffmpeg is not installed")
        config.PROXY_DIR.mkdir(parents=True, exist_ok=True)
        dest = config.PROXY_DIR / f"m{match.id}.mp4"
        tmp = dest.with_suffix(".part.mp4")
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error", "-nostats", "-progress", "pipe:1",
            "-i", match.video_path, "-map", "0:v:0", "-map", "0:a:0?",
            "-vf", "scale=w='min(1920,iw)':h='min(1080,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
            "-force_key_frames", "expr:gte(t,n_forced*2)",  # keyframe every 2s: fast seeking
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(tmp),
        ]
        match.proxy_status, match.proxy_progress = "pending", 0.0
        db.commit()
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        last = 0.0
        for line in proc.stdout:  # ffmpeg -progress writes key=value lines
            m = _OUT_TIME.match(line.strip())
            if m and match.duration_s:
                p = min(0.99, int(m.group(1)) / 1e6 / match.duration_s)
                if p - last >= 0.02:
                    match.proxy_progress, last = round(p, 3), p
                    db.commit()
        stderr = proc.stderr.read()
        if proc.wait() != 0 or not tmp.exists():
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"ffmpeg failed: {stderr.strip()[:300]}")
        tmp.replace(dest)
        match.proxy_path, match.proxy_status, match.proxy_progress = str(dest), "ready", 1.0
        db.commit()
    except Exception:  # noqa: BLE001 - playback falls back to the original
        db.rollback()
        log.exception("building proxy for match %s failed", match_id)
        match = db.get(Match, match_id)
        if match is not None:
            match.proxy_status = "failed"
            db.commit()
    finally:
        db.close()
