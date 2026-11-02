"""Decode video frames with ffmpeg (no OpenCV): raw RGB numpy arrays at a chosen rate."""
import subprocess
from collections.abc import Iterator
from pathlib import Path

import numpy as np


def read_frames(path: Path, fps: float, width: int, height: int, scale: float = 1.0) -> Iterator[tuple[float, np.ndarray]]:
    """Yield (t seconds, HxWx3 uint8 RGB) sampled at `fps`. `scale` < 1 decodes smaller frames
    (detection coordinates must then be divided by `scale` to get back to full-size pixels)."""
    w, h = int(width * scale) // 2 * 2, int(height * scale) // 2 * 2
    proc = subprocess.Popen(
        ["ffmpeg", "-loglevel", "error", "-i", str(path), "-vf", f"fps={fps},scale={w}:{h}",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    size, n = w * h * 3, 0
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield n / fps, np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            n += 1
    finally:
        proc.stdout.close()
        err = proc.stderr.read().decode(errors="replace")
        if proc.wait() not in (0, -15) and n == 0:
            raise RuntimeError(f"ffmpeg could not decode {path}: {err.strip()[:300]}")
