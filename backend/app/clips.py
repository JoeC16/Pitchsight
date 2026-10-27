"""Clip cutting with ffmpeg. Clips are cached on disk by (match, start, end)."""
import shutil
import subprocess
from pathlib import Path

from . import config
from .models import Event, Match


class ClipError(RuntimeError):
    pass


def clip_window(event: Event, duration_s: float | None) -> tuple[float, float]:
    start = max(0.0, event.t_start - config.CLIP_PRE_S)
    end = event.t_end + config.CLIP_POST_S
    if duration_s:
        end = min(end, duration_s)
    return start, max(end, start + 1.0)


def clip_path(match: Match, start: float, end: float) -> Path:
    return config.CLIP_DIR / f"m{match.id}_{int(start * 1000)}_{int(end * 1000)}.mp4"


def get_or_cut_clip(match: Match, event: Event) -> Path:
    if not match.video_path:
        raise ClipError("Match has no video")
    start, end = clip_window(event, match.duration_s)
    dest = clip_path(match, start, end)
    if dest.exists():
        return dest
    if not shutil.which("ffmpeg"):
        raise ClipError("ffmpeg is not installed")

    config.CLIP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part.mp4")
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-i", match.video_path, "-t", f"{end - start:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-movflags", "+faststart", str(tmp),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise ClipError(f"ffmpeg failed: {result.stderr.strip()[:300]}")
    tmp.replace(dest)  # atomic, so a half-written clip is never served from cache
    return dest
