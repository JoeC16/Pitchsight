"""Synthetic 'panoramic camera' video of a GAA pitch, for testing calibration end to end.

    backend/.venv/bin/python e2e/make_pitch_video.py OUT_DIR

Draws the pitch markings from the sport pack through a known perspective camera into a
1920x540 frame, makes a 40 s mp4, and writes landmarks.json with every landmark's true pixel
position. This is a geometry test fixture, not footage: no players, no ball.
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app import pitch, taxonomy  # noqa: E402

W_PX, H_PX, L, WID = 1920, 540, 140.0, 85.0


def camera() -> np.ndarray:
    """Pitch metres -> pixels: near sideline low and wide, far sideline high and narrow."""
    src = [(0, 0), (L, 0), (0, WID), (L, WID)]
    dst = [(60, 500), (1860, 500), (460, 170), (1460, 170)]
    A, b = [], []
    for (x, y), (u, v) in zip(src, dst):
        A += [[x, y, 1, 0, 0, 0, -u * x, -u * y], [0, 0, 0, x, y, 1, -v * x, -v * y]]
        b += [u, v]
    return np.append(np.linalg.solve(np.array(A, float), np.array(b, float)), 1).reshape(3, 3)


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    pack, C = taxonomy.get_pack("gaa_football"), camera()
    img = np.zeros((H_PX, W_PX, 3), np.uint8)
    img[:] = (34, 110, 52)  # grass
    for (x0, y0), (x1, y1) in pitch.segments(pack, L, WID):
        ts = np.linspace(0, 1, 2000)
        pts = pitch.apply(C, np.c_[x0 + (x1 - x0) * ts, y0 + (y1 - y0) * ts])
        for u, v in pts.round().astype(int):
            img[max(0, v - 1):v + 2, max(0, u - 1):u + 2] = 240  # 3px white line
    ppm = out / "frame.ppm"
    ppm.write_bytes(f"P6 {W_PX} {H_PX} 255\n".encode() + img.tobytes())
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(ppm), "-t", "40", "-r", "10",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "20", str(out / "pitch.mp4")], check=True)
    marks = pitch.landmarks(pack, L, WID)
    px = pitch.apply(C, [[m.x, m.y] for m in marks])
    (out / "landmarks.json").write_text(json.dumps(
        {"width": W_PX, "height": H_PX, "landmarks": {m.id: [float(u), float(v)] for m, (u, v) in zip(marks, px)}}, indent=1))
    ppm.unlink()
    print(out / "pitch.mp4")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
