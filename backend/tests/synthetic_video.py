"""Render synthetic 'panoramic camera' match video from a tracking scenario (test fixture).

Pitch markings from the sport pack, players as kit-coloured rectangles scaled by perspective,
the ball as an orange dot, all through a known camera homography. Lets the whole video -> events
path be tested end to end with known answers. Not footage: no lighting, occlusion or blur.
"""
import subprocess
from pathlib import Path

import numpy as np

from app import pitch, taxonomy
from app.pipeline.tracking import Tracking

W_PX, H_PX = 960, 400
L, WID = 140.0, 85.0
HOME_RGB, AWAY_RGB = (200, 20, 30), (20, 50, 200)        # red v blue
BALL_RGB = (255, 140, 0)


def camera() -> np.ndarray:
    """Pitch metres -> pixels: near sideline low and wide, far sideline high and narrow."""
    src = [(0, 0), (L, 0), (0, WID), (L, WID)]
    dst = [(20, 380), (940, 380), (230, 90), (730, 90)]
    A, b = [], []
    for (x, y), (u, v) in zip(src, dst):
        A += [[x, y, 1, 0, 0, 0, -u * x, -u * y], [0, 0, 0, x, y, 1, -v * x, -v * y]]
        b += [u, v]
    return np.append(np.linalg.solve(np.array(A, float), np.array(b, float)), 1).reshape(3, 3)


def pitch_setup(home_attacks_first: str = "right") -> dict:
    """What a coach's pitch setup would hold for this camera (exact calibration, kit colours)."""
    C = camera()
    return {"length": L, "width": WID, "home_attacks_first": home_attacks_first,
            "H": np.linalg.inv(C).tolist(), "H_inv": C.tolist(),
            "home_colour": "#%02x%02x%02x" % HOME_RGB, "away_colour": "#%02x%02x%02x" % AWAY_RGB}


def _background(C) -> np.ndarray:
    img = np.zeros((H_PX, W_PX, 3), np.uint8)
    img[:] = (34, 110, 52)
    for (x0, y0), (x1, y1) in pitch.segments(taxonomy.get_pack("gaa_football"), L, WID):
        ts = np.linspace(0, 1, 1500)
        for u, v in pitch.apply(C, np.c_[x0 + (x1 - x0) * ts, y0 + (y1 - y0) * ts]).round().astype(int):
            if 0 <= v < H_PX and 0 <= u < W_PX:
                img[v, u] = 240
    return img


def render(tracking: Tracking, out: Path) -> Path:
    C, bg = camera(), None
    bg = _background(C)
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W_PX}x{H_PX}",
         "-r", str(tracking.fps), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
         "-g", str(int(tracking.fps)), str(out)], stdin=subprocess.PIPE)
    for f in tracking.frames:
        img = bg.copy()
        for p in f.players:
            (u, v), (u2, v2) = pitch.apply(C, [(p.x, p.y), (p.x + 1.0, p.y)])
            scale = abs(u2 - u)                              # pixels per metre at this spot
            w, h = max(3, int(0.7 * scale)), max(6, int(1.9 * scale))
            x1, y2 = int(u - w / 2), int(v)
            img[max(0, y2 - h):y2, max(0, x1):x1 + w] = HOME_RGB if p.team == "home" else AWAY_RGB
        if f.ball:
            (u, v), = pitch.apply(C, [f.ball])
            u, v = int(u), int(v)
            img[max(0, v - 2):v + 2, max(0, u - 2):u + 2] = BALL_RGB
        proc.stdin.write(img.tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg failed to encode the synthetic video")
    return out
