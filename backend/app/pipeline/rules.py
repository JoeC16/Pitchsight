"""Rule-based events from tracking data: kickouts, shots and turnovers, each with a confidence.

Everything here works in pitch metres (see pitch.py) and only emits event types and attribute
values that the sport pack defines. Thresholds that are sport-specific live in the pack's
`detection` section. Untested on real footage: these are starting rules to be tuned against
coach-verified matches.

Known limits, by design rather than by accident:
  - A ball in the air is projected onto the ground by the calibration, so its position is wrong
    while it is high. Shot outcome is therefore only set when it's clearly wide; goal / point /
    saved need either a better ball model or a human, and are left for review.
  - Frees need the referee's whistle, which tracking can't see; they are not detected.
"""
import math
from collections.abc import Callable
from dataclasses import dataclass

from .. import pitch
from .interfaces import EventDraft
from .possession import Possession, PossessionParams, controller, possessions
from .tracking import Tracking


@dataclass
class Kick:
    i: int                      # frame index where the ball first moves fast
    t: float
    origin: tuple[float, float]
    team: str | None            # kicker's team (nearest player just before the kick)
    stationary_s: float         # how long the ball sat still before (set pieces)


@dataclass
class RuleParams:
    kick_speed: float = 14.0        # m/s: ball faster than this = struck
    stationary_speed: float = 0.7   # m/s: slower than this = ball at rest
    set_piece_s: float = 1.5        # at rest at least this long before a kick = set piece
    kickout_follow_s: float = 8.0   # look this far ahead for who wins a kickout
    shot_window_s: float = 3.0      # follow a shot's flight this long
    turnover_max_gap_s: float = 6.0


def _clamp(c: float) -> float:
    return round(max(0.05, min(0.95, c)), 2)


def _lane(y: float, width: float, attacking_right: bool, values: list[str]) -> str:
    ay = (width - y) if attacking_right else y  # left = attacker's left (see pitch.zone_attributes)
    return values[min(len(values) - 1, max(0, int(ay / width * len(values))))]


def find_kicks(tr: Tracking, speeds: list[float | None], rp: RuleParams) -> list[Kick]:
    kicks, f = [], tr.frames
    for i in range(1, len(f)):
        s, prev = speeds[i], speeds[i - 1]
        if s is None or s < rp.kick_speed or (prev is not None and prev >= rp.kick_speed):
            continue
        origin = f[i - 1].ball
        if origin is None:
            continue
        # The kicker: last player within 3m of the ball in the half second before it took off.
        team = None
        for j in range(i - 1, -1, -1):
            if f[i - 1].t - f[j].t > 0.5:
                break
            ball = f[j].ball
            near = min((p for p in f[j].players if p.team), default=None,
                       key=lambda p: math.dist(ball, (p.x, p.y))) if ball else None
            if near and math.dist(ball, (near.x, near.y)) < 3.0:
                team = near.team
                break
        # How long was the ball at rest before this?
        j = i - 1
        while j > 0 and speeds[j] is not None and speeds[j] < rp.stationary_speed:
            j -= 1
        kicks.append(Kick(i, f[i - 1].t, origin, team, f[i - 1].t - f[j].t))
    return kicks


def detect_events(tr: Tracking, pack: dict, setup: dict, period_of: Callable[[float], int],
                  rp: RuleParams | None = None, pp: PossessionParams | None = None) -> list[EventDraft]:
    rp = rp or RuleParams()
    types, det = pack["_types"], pack.get("detection", {})
    L, W, first = setup["length"], setup["width"], setup["home_attacks_first"]
    speeds = tr.ball_speeds()
    poss = possessions(tr, pp)
    kicks = find_kicks(tr, speeds, rp)

    def attacks_right(team: str, t: float) -> bool:
        return pitch.attacking_right(team, period_of(t), first)

    def next_possession(t: float, within: float) -> Possession | None:
        return next((p for p in poss if t < p.t_start <= t + within), None)

    def first_control(k: Kick, within: float):
        """(team, player id, time, ball xy) of the first player to control the ball after a kick.
        Frame by frame, because a kick to a teammate doesn't start a new possession."""
        cp = pp or PossessionParams()
        for f, s in zip(tr.frames[k.i:], speeds[k.i:]):
            if f.t > k.t + within:
                return None
            c = controller(f, s, cp)
            if c:
                return c[0], c[1], f.t, f.ball
        return None

    drafts: list[EventDraft] = []
    restarts: list[float] = []  # times of kickouts/shots: possession changes there aren't turnovers

    # ---- kickouts: ball placed near an end's kickout line, at rest, then struck
    ko = det.get("kickout")
    for k in kicks:
        if not ko or "kickout" not in types or k.stationary_s < rp.set_piece_s:
            continue
        x, y = k.origin
        end_left = x < L / 2
        from_end = x if end_left else L - x
        if not (ko["from_end_m"][0] <= from_end <= ko["from_end_m"][1] and abs(y - W / 2) <= ko["max_from_centre_m"]):
            continue
        # The kicking team defends this end, i.e. attacks the other one.
        team = "home" if attacks_right("home", k.t) == end_left else "away"
        attrs, conf = {}, 0.5 + (0.15 if k.stationary_s >= 2.5 else 0) + (0.1 if k.team == team else 0)
        allowed = {a["id"]: a["values"] for a in types["kickout"]["attributes"]}
        won = first_control(k, rp.kickout_follow_s)
        if won:
            won_team, won_player, won_t, land = won
            dist = math.dist(k.origin, land)
            for band, (lo, hi) in ko.get("length_m", {}).items():
                if lo <= dist < hi and band in allowed.get("length", []):
                    attrs["length"] = band
            if "target" in allowed:
                attrs["target"] = _lane(land[1], W, attacks_right(team, k.t), allowed["target"])
            # Clean = won within 3s with no opponent close to the receiver; otherwise a break.
            fr = next(f for f in tr.frames[k.i:] if f.t == won_t)
            receiver = next((p for p in fr.players if p.id == won_player), None)
            contested = receiver and any(p.team and p.team != won_team and math.dist((p.x, p.y), (receiver.x, receiver.y)) < 2.5
                                         for p in fr.players)
            clean = won_t - k.t <= 3.0 and not contested
            outcome = f"{'retained' if won_team == team else 'lost'}_{'clean' if clean else 'break'}"
            if outcome in allowed.get("outcome", []):
                attrs["outcome"] = outcome
            conf += 0.15
        drafts.append(EventDraft("kickout", team, period_of(k.t), round(k.t, 2), round(k.t + 3, 2), attrs, _clamp(conf)))
        restarts.append(k.t)

    # ---- shots: a struck ball from within range, heading at the goal the kicker attacks,
    # that the kicking team doesn't win back inside the pitch (otherwise it was a pass)
    sh = det.get("shot")
    for k in kicks:
        if not sh or "shot" not in types or k.team is None or k.t in restarts:
            continue
        right = attacks_right(k.team, k.t)
        goal_x = L if right else 0.0
        if math.dist(k.origin, (goal_x, W / 2)) > sh["max_distance_m"]:
            continue
        # Follow the flight.
        path = [f.ball for f in tr.frames[k.i:] if f.t <= k.t + rp.shot_window_s and f.ball]
        if len(path) < 2:
            continue
        dx = path[-1][0] - k.origin[0]
        if (dx <= 0) if right else (dx >= 0):
            continue  # not going toward that goal
        crossed = next((pt for pt in path if (pt[0] >= L if right else pt[0] <= 0)), None)
        if crossed is None:  # extrapolate the last segment to the end line
            (x0, y0), (x1, y1) = path[-2], path[-1]
            if x1 == x0:
                continue
            y_end = y0 + (y1 - y0) * (goal_x - x0) / (x1 - x0)
        else:
            y_end = crossed[1]
        if abs(y_end - W / 2) > 25:
            continue  # heading well away from the goal: a cross-field kick, not a shot
        regained = first_control(k, rp.shot_window_s)
        if regained and regained[0] == k.team and crossed is None:
            continue  # a teammate won it: that was a pass
        allowed = {a["id"]: a["values"] for a in types["shot"]["attributes"]}
        attrs = pitch.zone_attributes(pack, "shot", *k.origin, L, W, right)
        if "source" in allowed:
            src = "free" if k.stationary_s >= rp.set_piece_s else "from_play"
            if src in allowed["source"]:
                attrs["source"] = src
        goal_half = pack["pitch"].get("goal_width", 6) / 2
        conf = 0.45 + (0.2 if crossed else 0) + (0.1 if math.dist(k.origin, (goal_x, W / 2)) < 40 else 0)
        if crossed and abs(y_end - W / 2) > goal_half + 2 and "wide" in allowed.get("outcome", []):
            attrs["outcome"] = "wide"  # crossed the end line clearly outside the posts
            conf += 0.1
        drafts.append(EventDraft("shot", k.team, period_of(k.t), round(k.t, 2), round(k.t + 3, 2), attrs, _clamp(conf)))
        restarts.append(k.t)

    # ---- turnovers: possession passes from one team to the other in open play
    if "turnover" in types:
        allowed = {a["id"]: a["values"] for a in types["turnover"]["attributes"]}
        for a, b in zip(poss, poss[1:]):
            gap = b.t_start - a.t_end
            if a.team == b.team or gap > rp.turnover_max_gap_s:
                continue
            if any(a.t_end - 1.0 <= r <= b.t_start + 0.5 for r in restarts):
                continue  # change came from a kickout or a shot, not a turnover
            attrs = pitch.zone_attributes(pack, "turnover", *b.start_xy, L, W, attacks_right(b.team, b.t_start))
            if "kind" in allowed:
                in_gap = [s for f, s in zip(tr.frames, speeds) if a.t_end < f.t < b.t_start and s is not None]
                if gap > 0.4 and in_gap and max(in_gap) > 8.0:
                    attrs["kind"] = "interception"   # ball was travelling (a pass) when it was won
                elif gap <= 0.4:
                    attrs["kind"] = "tackle"         # won straight off the carrier
            conf = 0.35 + 0.25 * min(1, a.duration / 2) + 0.25 * min(1, b.duration / 2) + 0.15 * (1 - gap / rp.turnover_max_gap_s)
            t = b.t_start
            drafts.append(EventDraft("turnover", b.team, period_of(t), round(t, 2), round(t + 3, 2), attrs, _clamp(conf)))

    drafts.sort(key=lambda d: d.t_start)
    return drafts
