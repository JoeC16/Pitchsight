"""Possession timeline from tracking: which team has the ball, when.

A team controls the ball in a frame when its player is the nearest to the ball, within
`control_radius`, and the ball is moving slowly enough to be carried. Short flickers (a ball
passing near an opponent) are ignored via `min_hold_s`; a team keeps possession across loose-ball
gaps (passes, bounces) until the other team gains control or play stops for `max_gap_s`.
"""
import math
from dataclasses import dataclass

from .tracking import Tracking


@dataclass
class PossessionParams:
    control_radius: float = 1.5    # metres from ball to player
    max_carry_speed: float = 9.0   # m/s; faster than this the ball is in flight, not controlled
    min_hold_s: float = 0.8        # control shorter than this doesn't count
    max_gap_s: float = 8.0         # loose for longer than this: possession ends (stoppage)


@dataclass
class Possession:
    team: str
    t_start: float          # first frame of control
    t_end: float            # last frame of control
    start_xy: tuple[float, float]
    end_xy: tuple[float, float]
    start_player: int

    @property
    def duration(self) -> float:
        return self.t_end - self.t_start


def controller(frame, speed: float | None, p: PossessionParams):
    """(team, player id) controlling the ball in this frame, or None."""
    if frame.ball is None or (speed is not None and speed > p.max_carry_speed):
        return None
    best = min(((math.dist(frame.ball, (pl.x, pl.y)), pl) for pl in frame.players if pl.team), default=None,
               key=lambda d: d[0])
    if best is None or best[0] > p.control_radius:
        return None
    return best[1].team, best[1].id


def possessions(tracking: Tracking, p: PossessionParams | None = None) -> list[Possession]:
    p = p or PossessionParams()
    speeds = tracking.ball_speeds()
    # 1. Runs of consecutive frames controlled by the same team.
    runs: list[list] = []  # [team, first_idx, last_idx, player]
    for i, (frame, speed) in enumerate(zip(tracking.frames, speeds)):
        c = controller(frame, speed, p)
        if c is None:
            continue
        team, player = c
        if runs and runs[-1][0] == team and i == runs[-1][2] + 1:
            runs[-1][2] = i
        else:
            runs.append([team, i, i, player])
    fr = tracking.frames
    # 2. Drop flickers. A run's duration includes its last frame interval.
    step = 1 / tracking.fps if tracking.fps else 0
    runs = [r for r in runs if fr[r[2]].t - fr[r[1]].t + step >= p.min_hold_s]
    # 3. Merge same-team runs across loose-ball gaps (passes) unless play stopped.
    out: list[Possession] = []
    for team, a, b, player in runs:
        if out and out[-1].team == team and fr[a].t - out[-1].t_end <= p.max_gap_s:
            out[-1].t_end, out[-1].end_xy = fr[b].t, fr[b].ball
            continue
        out.append(Possession(team, fr[a].t, fr[b].t, fr[a].ball, fr[b].ball, player))
    return out
