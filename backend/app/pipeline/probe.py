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
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise ProbeError("No video stream found in the file")

    fmt = data.get("format", {})
    duration = float(fmt.get("duration") or video.get("duration") or 0)
    if duration <= 0:
        raise ProbeError("Could not determine video duration")

    fps = None
    rate = video.get("r_frame_rate", "")
    if "/" in rate:
        num, den = rate.split("/")
        if float(den) > 0:
            fps = round(float(num) / float(den), 3)
    return VideoInfo(
        duration_s=duration, fps=fps, width=video.get("width"), height=video.get("height"),
        video_codec=video.get("codec_name"), pix_fmt=video.get("pix_fmt"),
        audio_codec=audio.get("codec_name") if audio else None,
        bit_rate=int(fmt["bit_rate"]) if fmt.get("bit_rate") else None,
        max_keyframe_gap_s=keyframe_gap(path, duration),
    )


def keyframe_gap(path: Path, duration: float, window_s: float = 60.0) -> float | None:
    """Largest gap between keyframes in the first minute. Browsers seek to keyframes, so a
    file with keyframes far apart makes every reel jump slow."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey",
         "-read_intervals", f"%+{window_s}", "-show_entries", "frame=pts_time", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=120,
    )
    fields = (line.split(",")[0].strip() for line in result.stdout.splitlines())
    times = sorted(float(t) for t in fields if t and t != "N/A")
    if result.returncode != 0 or not times:
        return None
    # Include the stretch after the last keyframe seen (a whole minute with one keyframe is bad too).
    edges = times + [min(duration, window_s)]
    return round(max(b - a for a, b in zip(edges, edges[1:])), 2)
