"""Event rules on synthetic tracking scenarios (pitch metres, 10 fps). These check the logic;
they say nothing about accuracy on real footage."""
import math

import pytest

from app import taxonomy
from app.pipeline.interfaces import MatchContext, VideoInfo
from app.pipeline.possession import possessions
from app.pipeline.real import DetectionUnavailable, TrackingDetector, period_finder
from app.pipeline.rules import detect_events
from app.pipeline.teams import assign_teams
from app.pipeline.tracking import Frame, Player, Tracking, dump_json, load_json

GAA = taxonomy.get_pack("gaa_football")
SOCCER = taxonomy.get_pack("soccer")
SETUP = {"length": 140, "width": 85, "home_attacks_first": "right"}
FPS = 10
# Bystanders far from the action, so they never "control" the ball.
CROWD = [Player(90 + i, "home" if i % 2 else "away", 130.0 - i, 84.0) for i in range(6)]


class Scene:
    def __init__(self):
        self.frames: list[Frame] = []
        self.t = 0.0

    def _seg(self, dur, a, b, carrier=None, extra=()):
        n = int(round(dur * FPS))
        for k in range(n):
            f = k / n
            ball = (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
            players = list(CROWD) + list(extra)
            if carrier:
                team, pid = carrier
                players.append(Player(pid, team, ball[0] + 0.5, ball[1]))
            self.frames.append(Frame(round(self.t, 3), ball, players))
            self.t += 1 / FPS
        return self

    def carry(self, team, pid, a, b, dur=3.0, extra=()):
        return self._seg(dur, a, b, (team, pid), extra)

    def fly(self, a, b, dur):
        return self._seg(dur, a, b)

    def rest(self, team, pid, at, dur):
        return self._seg(dur, at, at, (team, pid))

    def tracking(self):
        return Tracking(FPS, self.frames)


def run(scene, pack=GAA):
    drafts = detect_events(scene.tracking(), pack, SETUP, lambda t: 1)
    for d in drafts:  # everything the rules emit must fit the sport pack
        taxonomy.validate_event(pack["sport"], d.type, d.team, d.attributes)
        assert 0 < d.confidence < 1
    return drafts


def test_interception_is_a_turnover_for_the_winning_team():
    s = Scene().carry("home", 1, (60, 40), (70, 40)).fly((70, 40), (80, 50), 0.6).carry("away", 2, (80, 50), (75, 50))
    [d] = run(s)
    assert d.type == "turnover" and d.team == "away"  # team_means: team that WON the ball
    assert d.t_start == pytest.approx(3.6, abs=0.15)
    # Away attack the left end in half 1: x=80 is 60m from their own end -> middle third.
    assert d.attributes == {"kind": "interception", "zone": "middle_third"}


def test_brief_touch_by_opponent_is_not_a_turnover():
    s = Scene().carry("home", 1, (60, 40), (65, 40))
    s._seg(0.3, (65, 40), (65.5, 40), ("away", 2))     # 0.3s flicker
    s.carry("home", 1, (65.5, 40), (72, 40))
    assert run(s) == []
    assert [p.team for p in possessions(s.tracking())] == ["home"]


def test_kickout_long_right_retained_clean():
    # Home attack right in half 1, so they kick out from the left end's 20m line.
    s = Scene().rest("home", 1, (20, 42.5), 3.0).fly((20, 42.5), (75, 20), 2.5).carry("home", 5, (75, 20), (80, 22))
    [d] = run(s)
    assert d.type == "kickout" and d.team == "home"
    assert d.attributes == {"length": "long", "target": "right", "outcome": "retained_clean"}
    assert d.confidence >= 0.8


def test_kickout_won_by_opponent_is_lost_and_not_also_a_turnover():
    s = Scene().rest("home", 1, (20, 42.5), 3.0).fly((20, 42.5), (50, 60), 1.5).carry("away", 7, (50, 60), (45, 60))
    [d] = run(s)
    assert d.type == "kickout" and d.attributes["outcome"] == "lost_clean" and d.attributes["length"] == "short"
    assert d.attributes["target"] == "left"


def test_wide_shot():
    # Away attack the left end (x=0) in half 1. Struck from 35m, crosses the end line ~22m off centre.
    s = Scene().carry("away", 3, (40, 30), (35, 35)).fly((35, 35), (-5, 18), 1.6)
    [d] = run(s)
    assert d.type == "shot" and d.team == "away"
    assert d.attributes == {"zone": "inside_45", "side": "centre", "source": "from_play", "outcome": "wide"}


def test_shot_between_the_posts_leaves_outcome_for_review():
    s = Scene().carry("away", 3, (35, 40), (30, 40)).fly((30, 40), (-1, 42), 1.2)
    [d] = run(s)
    assert d.type == "shot" and "outcome" not in d.attributes  # goal vs point vs saved: not knowable here


def test_free_kick_shot_is_source_free():
    s = Scene().rest("home", 4, (110, 40), 3.0).fly((110, 40), (141, 43), 1.4)
    [d] = run(s)
    assert d.type == "shot" and d.team == "home" and d.attributes["source"] == "free"


def test_long_pass_to_a_teammate_is_not_a_shot():
    s = Scene().carry("away", 3, (45, 40), (40, 40)).fly((40, 40), (6, 46), 1.4).carry("away", 8, (6, 46), (8, 46))
    assert run(s) == []


def test_kick_away_from_goal_is_not_a_shot():
    s = Scene().carry("away", 3, (40, 40), (35, 40)).fly((35, 40), (60, 70), 1.3).carry("home", 4, (60, 70), (62, 70))
    drafts = run(s)
    assert [d.type for d in drafts] == ["turnover"]  # it's just possession going over


def test_soccer_pack_gets_only_its_own_event_types():
    s = Scene().rest("home", 1, (20, 34), 3.0).fly((20, 34), (60, 20), 2.0).carry("away", 2, (60, 20), (58, 20))
    setup = {"length": 105, "width": 68, "home_attacks_first": "right"}
    drafts = detect_events(s.tracking(), SOCCER, setup, lambda t: 1)
    assert "kickout" not in {d.type for d in drafts}
    for d in drafts:
        taxonomy.validate_event("soccer", d.type, d.team, d.attributes)


def test_second_half_swaps_direction():
    # Same kick as the wide-shot test but in period 2: away now attack the right end, so it isn't a shot.
    s = Scene().carry("away", 3, (40, 30), (35, 35)).fly((35, 35), (-2, 20), 1.5)
    assert detect_events(s.tracking(), GAA, SETUP, lambda t: 2) == []


def test_period_finder():
    f = period_finder(None, 4200, 2)
    assert (f(10), f(2099), f(2100), f(4100)) == (1, 1, 2, 2)
    g = period_finder([120, 2400], 4200, 2)
    assert (g(0), g(500), g(2500)) == (1, 1, 2)


def test_tracking_json_round_trip(tmp_path):
    s = Scene().carry("home", 1, (60, 40), (70, 40), dur=1.0)
    dump_json(s.tracking(), tmp_path / "t.json")
    back = load_json(tmp_path / "t.json")
    assert len(back.frames) == 10 and back.frames[3].players[-1].team == "home"


def test_tracking_detector_needs_setup_and_tracking(tmp_path):
    video = tmp_path / "m.mp4"
    video.write_bytes(b"x")
    info = VideoInfo(duration_s=60)
    with pytest.raises(DetectionUnavailable, match="pitch"):
        TrackingDetector().detect(video, info, GAA, lambda p: None, MatchContext("gaa_football"))
    ctx = MatchContext("gaa_football", pitch_setup=SETUP)
    with pytest.raises(DetectionUnavailable, match="isn't built yet"):
        TrackingDetector().detect(video, info, GAA, lambda p: None, ctx)
    s = Scene().carry("home", 1, (60, 40), (70, 40)).fly((70, 40), (80, 50), 0.6).carry("away", 2, (80, 50), (75, 50))
    dump_json(s.tracking(), tmp_path / "m.mp4.tracking.json")
    drafts = TrackingDetector().detect(video, info, GAA, lambda p: None, ctx)
    assert [d.type for d in drafts] == ["turnover"]


def test_team_assignment_from_kit_colours():
    red, blue = (200, 30, 40), (30, 60, 190)
    tracks = {1: (190, 35, 45), 2: (210, 25, 30), 3: (180, 40, 50),      # reds, a bit varied by light
              4: (35, 65, 180), 5: (25, 50, 200), 6: (40, 70, 170),      # blues
              7: (240, 230, 30),                                          # yellow keeper
              8: (20, 20, 20)}                                            # referee in black
    got = assign_teams(tracks, red, blue)
    assert {k: v for k, v in got.items() if v == "home"}.keys() == {1, 2, 3}
    assert {k: v for k, v in got.items() if v == "away"}.keys() == {4, 5, 6}
    assert got[7] is None and got[8] is None
    assert assign_teams({}, red, blue) == {}


def test_real_detector_through_the_app(client, match, monkeypatch, tmp_path):
    from app import config
    from app.pipeline import runner
    from tests.test_api import upload

    monkeypatch.setattr(config, "DETECTOR", "real")
    monkeypatch.setattr(runner, "probe", lambda path: VideoInfo(duration_s=600, fps=25, width=1920, height=1080,
                                                               video_codec="h264", pix_fmt="yuv420p", audio_codec="aac"))
    m = match["id"]
    upload(client, m)
    failed = client.get(f"/api/matches/{m}").json()
    assert failed["status"] == "failed" and "Set up the pitch" in failed["error"]

    # Coach sets up the pitch (exact 4-point fit is enough here), then tracking arrives.
    corners = [("left.corner.near", 0, 1000), ("right.corner.near", 1400, 1000),
               ("left.corner.far", 300, 300), ("right.corner.far", 1100, 300)]
    client.put(f"/api/matches/{m}/pitch", json={"home_attacks_first": "right", "points": [
        {"landmark": lm, "u": u, "v": v} for lm, u, v in corners]})
    s = Scene().rest("home", 1, (20, 42.5), 3.0).fly((20, 42.5), (75, 20), 2.5).carry("home", 5, (75, 20), (80, 22)) \
        .fly((80, 22), (85, 30), 0.5).carry("away", 6, (85, 30), (80, 30))
    from app.db import SessionLocal
    from app.models import Match
    with SessionLocal() as db:
        video_path = db.get(Match, m).video_path
    dump_json(s.tracking(), f"{video_path}.tracking.json")

    assert client.post(f"/api/matches/{m}/reprocess").status_code == 200
    ready = client.get(f"/api/matches/{m}").json()
    assert ready["status"] == "ready" and ready["detector"] == "ai"
    events = client.get(f"/api/matches/{m}/events").json()
    assert [(e["type"], e["team"], e["origin"]) for e in events] == [("kickout", "home", "ai"), ("turnover", "away", "ai")]
    # And it can be measured like any run once a coach checks the stretch.
    for e in events:
        client.patch(f"/api/events/{e['id']}", json={"reviewed": True})
    client.post(f"/api/matches/{m}/verified-spans", json={"t_start": 0, "t_end": 20})
    ev = client.get(f"/api/matches/{m}/evaluation").json()
    assert ev["demo"] is False and ev["overall"]["precision"] == 1.0
