"""Video -> tracking data: decode, detect, project to the pitch, track, assign teams.

The output is the same Tracking structure the event rules consume (and that external trackers
can supply as JSON), so detection quality can be improved without touching the rules.
"""
import numpy as np

from .. import pitch
from .detection import FrameDetector
from .teams import assign_teams, hex_rgb
from .track import Tracker
from .tracking import Frame, Player, Tracking
from .video import read_frames


def _kit_colour(frame: np.ndarray, box) -> tuple[int, int, int] | None:
    """Median colour of the upper body (top 60% of the box), ignoring grass-green pixels."""
    x1, y1, x2, y2 = (int(round(v)) for v in box)
    crop = frame[y1:y1 + max(1, int((y2 - y1) * 0.6)), x1:x2].reshape(-1, 3).astype(int)
    if not len(crop):
        return None
    keep = crop[~((crop[:, 1] > crop[:, 0] + 25) & (crop[:, 1] > crop[:, 2] + 25))]
    return tuple(int(v) for v in np.median(keep if len(keep) else crop, axis=0))


def build_tracking(video, info, setup: dict, detector: FrameDetector, fps: float = 5.0, scale: float = 1.0,
                   progress=lambda p: None) -> Tracking:
    """Run the detector over the video and turn its boxes into pitch-coordinate tracking."""
    if not setup.get("home_colour") or not setup.get("away_colour"):
        raise ValueError("Add the home and away kit colours in the pitch setup: teams are told apart by kit")
    H = np.asarray(setup["H"], float)
    L, W = setup["length"], setup["width"]
    on_pitch = lambda x, y: -5 <= x <= L + 5 and -5 <= y <= W + 5  # noqa: E731 - ignore crowd, dugouts
    tracker, raw = Tracker(), []
    total = max(1, int(info.duration_s * fps))
    for n, (t, frame) in enumerate(read_frames(video, fps, info.width, info.height, scale)):
        dets = detector.detect(frame)
        players, ball = [], None
        for d in dets:
            x1, y1, x2, y2 = (v / scale for v in d.box)
            if d.cls == "ball":
                # Box centre: a ball in the air projects to the wrong spot (see rules.py).
                bx, by = pitch.apply(H, [((x1 + x2) / 2, (y1 + y2) / 2)])[0]
                if on_pitch(bx, by):
                    ball = (float(bx), float(by))
            elif d.cls == "player":
                fx, fy = pitch.apply(H, [((x1 + x2) / 2, y2)])[0]  # feet: bottom centre of the box
                if on_pitch(fx, fy):
                    players.append((float(fx), float(fy), _kit_colour(frame, d.box)))
        ids = tracker.update(t, players)
        raw.append((t, ball, [(tid, x, y) for tid, (x, y, _) in zip(ids, players)]))
        if n % 25 == 0:
            progress(min(0.9, n / total))
    # Teams from each track's typical kit colour, seeded with the declared kits.
    colours = {tid: tuple(int(v) for v in np.median(tr.colours, axis=0))
               for tid, tr in tracker.tracks.items() if tr.colours}
    team = assign_teams(colours, hex_rgb(setup["home_colour"]), hex_rgb(setup["away_colour"]))
    frames = [Frame(t, ball, [Player(tid, team.get(tid), x, y) for tid, x, y in ps]) for t, ball, ps in raw]
    return Tracking(fps, frames)
