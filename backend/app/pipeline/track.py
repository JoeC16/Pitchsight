"""Multi-object tracking in pitch coordinates: stable ids for players across frames.

Greedy nearest-neighbour association with a speed limit; a stand-in for ByteTrack (MIT) to be
swapped in once real detections (with their noise and occlusions) exist.
"""
import math
from dataclasses import dataclass, field


@dataclass
class Track:
    id: int
    x: float
    y: float
    last_t: float
    colours: list = field(default_factory=list)  # colour samples, for team assignment


class Tracker:
    def __init__(self, max_speed: float = 10.0, max_gap_s: float = 1.5):
        self.max_speed, self.max_gap_s = max_speed, max_gap_s  # m/s (a sprint is ~9), seconds unseen
        self.tracks: dict[int, Track] = {}
        self._next = 1

    def update(self, t: float, points: list[tuple[float, float, tuple]]) -> list[int]:
        """points: (x, y, colour) for this frame's players. Returns a track id per point."""
        live = [tr for tr in self.tracks.values() if t - tr.last_t <= self.max_gap_s]
        pairs = sorted(
            (math.dist((x, y), (tr.x, tr.y)), i, tr.id)
            for i, (x, y, _) in enumerate(points) for tr in live
            if math.dist((x, y), (tr.x, tr.y)) <= self.max_speed * max(t - tr.last_t, 0.1) + 1.0
        )
        ids: list[int | None] = [None] * len(points)
        used = set()
        for _, i, tid in pairs:
            if ids[i] is None and tid not in used:
                ids[i], used = tid, used | {tid}
        for i, (x, y, colour) in enumerate(points):
            if ids[i] is None:
                ids[i] = self._next
                self.tracks[self._next] = Track(self._next, x, y, t)
                self._next += 1
            tr = self.tracks[ids[i]]
            tr.x, tr.y, tr.last_t = x, y, t
            if colour is not None and len(tr.colours) < 50:
                tr.colours.append(colour)
        return ids
