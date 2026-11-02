"""Per-frame object detection: the swappable model behind the video pipeline.

A FrameDetector turns one RGB frame into boxes with a class. The real one will be a trained
model (RF-DETR, Apache-2.0, fine-tuned on Veo-style footage; the ball is a few pixels wide).
Select it with PITCHSIGHT_FRAME_DETECTOR. The only detector here today is `synthetic`, which
finds the solid-colour shapes drawn by tests/synthetic_video.py and is useless on real video.
"""
from collections import deque
from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass
class Detection:
    box: tuple[float, float, float, float]   # x1, y1, x2, y2 in frame pixels
    cls: str                                  # "player" | "ball" (later: "goalkeeper", "referee")
    score: float


class FrameDetector(Protocol):
    name: str

    def detect(self, frame: np.ndarray) -> list[Detection]: ...


class DetectorUnavailable(RuntimeError):
    pass


def _components(mask: np.ndarray, min_cells: int) -> list[tuple[int, int, int, int, int]]:
    """Bounding boxes (x1, y1, x2, y2, size) of 4-connected True regions. Visits only set pixels,
    so it's fast when the mask is sparse (a few dozen players on a pitch)."""
    ys, xs = np.nonzero(mask)
    todo = set(zip(ys.tolist(), xs.tolist()))
    out = []
    while todo:
        start = todo.pop()
        q, cells = deque([start]), [start]
        while q:
            y, x = q.popleft()
            for nb in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if nb in todo:
                    todo.remove(nb)
                    q.append(nb)
                    cells.append(nb)
        if len(cells) >= min_cells:
            cy, cx = zip(*cells)
            out.append((min(cx), min(cy), max(cx) + 1, max(cy) + 1, len(cells)))
    return out


class SyntheticBlobDetector:
    """FOR TESTS ONLY: finds the flat-coloured players and ball that tests/synthetic_video.py draws.
    Players are any strongly coloured blob that isn't grass, line-white or the ball colour."""

    name = "synthetic"
    BALL = np.array([255, 140, 0])        # orange, as drawn by the renderer

    def detect(self, frame: np.ndarray) -> list[Detection]:
        f = frame.astype(np.int16)
        r, g, b = f[..., 0], f[..., 1], f[..., 2]
        ball = (np.abs(f - self.BALL).sum(axis=2) < 90)
        ballish = (r > 140) & (g > 70) & (b < 110) & (r > b + 80)   # compression smears the ball's orange
        grass = (g > r + 25) & (g > b + 25)
        white = (r > 200) & (g > 200) & (b > 200)
        player = ~grass & ~white & ~ballish & ((f.max(axis=2) - f.min(axis=2)) > 60)
        out = [Detection((x1, y1, x2, y2), "player", 0.9) for x1, y1, x2, y2, _ in _components(player, 12)
               if (y2 - y1) >= 1.2 * (x2 - x1)]  # people are upright
        balls = sorted(_components(ball, 2), key=lambda c: -c[4])[:1]
        out += [Detection((x1, y1, x2, y2), "ball", 0.8) for x1, y1, x2, y2, _ in balls]
        return out


def get_frame_detector(name: str) -> FrameDetector:
    if name == "synthetic":
        return SyntheticBlobDetector()
    if name in ("", "none"):
        raise DetectorUnavailable(
            "No player/ball detection model is configured (PITCHSIGHT_FRAME_DETECTOR). Until one is, "
            "tracking can be supplied as <video>.tracking.json; use PITCHSIGHT_DETECTOR=stub for demo data.")
    raise DetectorUnavailable(f"Unknown frame detector {name!r}")
