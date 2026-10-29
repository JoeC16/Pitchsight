"""Tracking data: where every player and the ball is, over time, in pitch metres.

This is the output of detection + tracking + team assignment + calibration, and the input to
the event rules. Until a detection model is wired in, a tracker can hand it over as JSON:

    {"fps": 5,
     "frames": [{"t": 12.4, "ball": [61.2, 40.0],             # or null when not seen
                 "players": [[7, "home", 60.5, 39.1], ...]}]}  # [track id, home|away|null, x, y]
"""
import json
import math
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Player:
    id: int
    team: str | None  # "home" | "away" | None (referee, unassigned)
    x: float
    y: float


@dataclass
class Frame:
    t: float
    ball: tuple[float, float] | None
    players: list[Player] = field(default_factory=list)


@dataclass
class Tracking:
    fps: float
    frames: list[Frame]

    def ball_speeds(self) -> list[float | None]:
        """Ball speed (m/s) into each frame, None where the ball wasn't seen in this or the previous frame."""
        out: list[float | None] = [None]
        for a, b in zip(self.frames, self.frames[1:]):
            if a.ball is None or b.ball is None or b.t <= a.t:
                out.append(None)
            else:
                out.append(math.dist(a.ball, b.ball) / (b.t - a.t))
        return out


def load_json(path: Path) -> Tracking:
    data = json.loads(Path(path).read_text())
    frames = [
        Frame(t=float(f["t"]), ball=tuple(f["ball"]) if f.get("ball") else None,
              players=[Player(int(p[0]), p[1], float(p[2]), float(p[3])) for p in f.get("players", [])])
        for f in data["frames"]
    ]
    frames.sort(key=lambda f: f.t)
    return Tracking(fps=float(data.get("fps", 5)), frames=frames)


def dump_json(tracking: Tracking, path: Path) -> None:
    Path(path).write_text(json.dumps({"fps": tracking.fps, "frames": [
        {"t": f.t, "ball": list(f.ball) if f.ball else None, "players": [[p.id, p.team, p.x, p.y] for p in f.players]}
        for f in tracking.frames]}))
