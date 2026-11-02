"""Video -> tracking -> events, end to end, on rendered synthetic match video.

Proves the stages are wired correctly (decode, detect, project, track, teams, rules). It says
nothing about accuracy on real footage: the synthetic detector only works on these renders.
"""
import math
import shutil

import numpy as np
import pytest

from app import config, pitch
from app.pipeline.detection import DetectorUnavailable, SyntheticBlobDetector, get_frame_detector
from app.pipeline.probe import probe
from app.pipeline.rules import detect_events
from app.pipeline.track import Tracker
from app.pipeline.video_pipeline import build_tracking
from tests import synthetic_video as sv
from app.pipeline.tracking import Player
from tests.test_rules import GAA, Scene

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


# Other players, spread out as on a real pitch (players packed shoulder to shoulder merge into one
# blob on camera: a real occlusion problem the eventual tracker must handle, not this test's job).
SPREAD = [Player(90 + i, team, x, y) for i, (team, x, y) in enumerate([
    ("home", 100, 70), ("away", 110, 12), ("home", 40, 72), ("away", 55, 8), ("home", 115, 50), ("away", 30, 20)])]


def scenario() -> Scene:
    # Home kick out long and keep it, then lose it to an interception; away run it up to 45m and
    # shoot wide (from further out than the pack's shot range it wouldn't count as a shot).
    return (Scene(SPREAD).rest("home", 1, (20, 42.5), 3.0).fly((20, 42.5), (75, 20), 2.5)
            .carry("home", 5, (75, 20), (80, 22)).fly((80, 22), (85, 30), 0.5)
            .carry("away", 6, (85, 30), (45, 35), dur=5.0).fly((45, 35), (-5, 22), 1.8))


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    truth = scenario().tracking()
    video = sv.render(truth, tmp_path_factory.mktemp("synth") / "match.mp4")
    return truth, video


def test_tracker_keeps_ids_through_motion():
    tr = Tracker()
    a = tr.update(0.0, [(10, 10, None), (50, 50, None)])
    b = tr.update(0.2, [(51, 50.5, None), (11, 10.5, None)])   # order swapped, both moved ~1m
    assert b == [a[1], a[0]]
    c = tr.update(0.4, [(80, 80, None)])                        # far away: a new track
    assert c[0] not in a


def test_frame_detector_registry():
    assert isinstance(get_frame_detector("synthetic"), SyntheticBlobDetector)
    with pytest.raises(DetectorUnavailable, match="No player/ball detection model"):
        get_frame_detector("none")


def test_video_to_tracking_matches_the_truth(rendered):
    truth, video = rendered
    got = build_tracking(video, probe(video), sv.pitch_setup(), SyntheticBlobDetector(), fps=10)
    assert abs(len(got.frames) - len(truth.frames)) <= 1
    ball_err, team_ok, seen = [], 0, 0
    for g, t in zip(got.frames, truth.frames):
        if g.ball and t.ball:
            ball_err.append(math.dist(g.ball, t.ball))
        for tp in t.players:
            near = min(g.players, key=lambda p: math.dist((p.x, p.y), (tp.x, tp.y)), default=None)
            if near and math.dist((near.x, near.y), (tp.x, tp.y)) < 1.5:
                seen += 1
                team_ok += near.team == tp.team
    total = sum(len(t.players) for t in truth.frames)
    assert np.median(ball_err) < 1.0, f"ball off by {np.median(ball_err):.2f} m"
    assert seen / total > 0.9 and team_ok / seen > 0.95, (seen / total, team_ok / seen)


def test_video_gives_the_same_events_as_the_true_positions(rendered):
    truth, video = rendered
    setup = sv.pitch_setup()
    expected = detect_events(truth, GAA, setup, lambda t: 1)
    got = detect_events(build_tracking(video, probe(video), setup, SyntheticBlobDetector(), fps=10), GAA, setup, lambda t: 1)
    assert [(e.type, e.team) for e in expected] == [("kickout", "home"), ("turnover", "away"), ("shot", "away")]
    assert [(e.type, e.team) for e in got] == [(e.type, e.team) for e in expected]
    assert all(abs(a.t_start - b.t_start) <= 0.5 for a, b in zip(got, expected))
    assert got[0].attributes == expected[0].attributes  # kickout length / target / outcome survive the trip


def test_kit_colours_are_required(rendered):
    _, video = rendered
    setup = {k: v for k, v in sv.pitch_setup().items() if k != "home_colour"}
    with pytest.raises(ValueError, match="kit colours"):
        build_tracking(video, probe(video), setup, SyntheticBlobDetector(), fps=10)


def test_real_detector_from_video_through_the_app(client, match, rendered, monkeypatch):
    from tests.test_api import upload
    _, video = rendered
    monkeypatch.setattr(config, "DETECTOR", "real")
    monkeypatch.setattr(config, "FRAME_DETECTOR", "synthetic")
    monkeypatch.setattr(config, "DETECT_FPS", 10.0)
    m = match["id"]
    upload(client, m, data=video.read_bytes())
    assert "Set up the pitch" in client.get(f"/api/matches/{m}").json()["error"]

    # The coach clicks 5 landmarks (here: their true pixel positions) and picks the kit colours.
    info = client.get(f"/api/matches/{m}/pitch").json()
    want = {"left.corner.near", "right.corner.near", "left.45m.far", "right.45m.far", "halfway.near"}
    marks = [lm for lm in info["landmarks"] if lm["id"] in want]
    px = pitch.apply(sv.camera(), [[lm["x"], lm["y"]] for lm in marks])
    setup = sv.pitch_setup()
    r = client.put(f"/api/matches/{m}/pitch", json={
        "home_attacks_first": "right", "home_colour": setup["home_colour"], "away_colour": setup["away_colour"],
        "second_half_s": 600,  # this clip is all first half (without it the app guesses the midpoint)
        "points": [{"landmark": lm["id"], "u": u, "v": v} for lm, (u, v) in zip(marks, px)]})
    assert r.status_code == 200 and r.json()["rms_error_m"] < 0.05

    client.post(f"/api/matches/{m}/reprocess")
    done = client.get(f"/api/matches/{m}").json()
    assert done["status"] == "ready" and done["detector"] == "ai", done["error"]
    events = client.get(f"/api/matches/{m}/events").json()
    assert [(e["type"], e["team"], e["origin"]) for e in events] == [
        ("kickout", "home", "ai"), ("turnover", "away", "ai"), ("shot", "away", "ai")]
