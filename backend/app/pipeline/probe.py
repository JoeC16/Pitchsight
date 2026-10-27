import json
import shutil
import subprocess
from pathlib import Path

from .interfaces import VideoInfo


class ProbeError(RuntimeError):
    pass


def probe(path: Path) -> VideoInfo:
    if not shutil.which("ffprobe"):
        raise ProbeError("ffprobe not found - install ffmpeg")
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise ProbeError("Could not read the video file - is it a valid mp4/mov?")
    data = json.loads(result.stdout)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        raise ProbeError("No video stream found in the file")

    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    if duration <= 0:
        raise ProbeError("Could not determine video duration")

    fps = None
    rate = video.get("r_frame_rate", "")
    if "/" in rate:
        num, den = rate.split("/")
        if float(den) > 0:
            fps = round(float(num) / float(den), 3)
    return VideoInfo(duration_s=duration, fps=fps, width=video.get("width"), height=video.get("height"))
