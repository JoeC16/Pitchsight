import shutil
import subprocess

import numpy as np
import pytest

from app import pitch, taxonomy
from tests.test_api import upload

GAA = taxonomy.get_pack("gaa_football")
SOCCER = taxonomy.get_pack("soccer")
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")



def _exact_homography(src, dst):
    """Plain 8x8 solve (independent of pitch.fit_homography) for a test camera."""
    A, b = [], []
    for (x, y), (u, v) in zip(src, dst):
        A += [[x, y, 1, 0, 0, 0, -u * x, -u * y], [0, 0, 0, x, y, 1, -v * x, -v * y]]
        b += [u, v]
    h = np.linalg.solve(np.array(A, float), np.array(b, float))
    return np.append(h, 1).reshape(3, 3)


# A plausible fixed panoramic camera on the near side, 3840x1080 frame: near sideline low and
# wide, far sideline higher and narrower. Maps pitch metres -> pixels.
CAMERA = _exact_homography([(0, 0), (140, 0), (0, 85), (140, 85)],
                           [(100, 1000), (3740, 1000), (900, 330), (2940, 330)])


def project(points):
    return pitch.apply(CAMERA, points)


def test_landmarks_and_segments_from_pack():
    marks = {m.id: m for m in pitch.landmarks(GAA, 140, 85)}
    assert (marks["left.45m.near"].x, marks["left.45m.near"].y) == (45, 0)
    assert (marks["right.13m.far"].x, marks["right.13m.far"].y) == (127, 85)
    assert marks["halfway.near"].x == 70
    assert marks["right.large rectangle.near.front"].x == 140 - 13
    assert marks["left.post.far"].y == pytest.approx(85 / 2 + 3.25)
    assert len(pitch.segments(GAA, 140, 85)) == 5 + 3 * 2 + 2 * 2 * 3
    assert {m.id for m in pitch.landmarks(SOCCER, 105, 68)} >= {"left.penalty area.near.front", "halfway.far"}


def test_dims_validated():
    assert pitch.dims(GAA) == (140, 85)
    with pytest.raises(pitch.CalibrationError, match="outside"):
        pitch.dims(GAA, length=200)


def test_homography_recovers_camera_from_clicks():
    marks = pitch.landmarks(GAA, 140, 85)
    chosen = [m for m in marks if m.id in {"left.corner.near", "right.corner.near", "left.45m.far",
                                            "right.45m.far", "halfway.near", "left.20m.far"}]
    px = project([[m.x, m.y] for m in chosen])
    rng = np.random.default_rng(1)
    noisy = px + rng.normal(0, 1.5, px.shape)  # ~1.5px click error
    fit = pitch.calibrate(GAA, 140, 85, [{"landmark": m.id, "u": u, "v": v} for m, (u, v) in zip(chosen, noisy)])
    assert fit["overdetermined"] and fit["rms_error_px"] < 3 and fit["rms_error_m"] < 0.5
    # A point that wasn't clicked lands where it should.
    centre_px = project([[70, 42.5]])
    assert pitch.apply(fit["H"], centre_px)[0] == pytest.approx([70, 42.5], abs=0.5)


def test_bad_calibrations_rejected():
    marks = pitch.landmarks(GAA, 140, 85)
    near = [m for m in marks if m.y == 0][:5]  # all on the near sideline: a line
    with pytest.raises(pitch.CalibrationError, match="in a line"):
        pitch.calibrate(GAA, 140, 85, [{"landmark": m.id, "u": u, "v": v} for m, (u, v) in zip(near, project([[m.x, m.y] for m in near]))])
    with pytest.raises(pitch.CalibrationError, match="unknown landmark"):
        pitch.calibrate(GAA, 140, 85, [{"landmark": "moon", "u": 0, "v": 0}] * 4)
    with pytest.raises(pitch.CalibrationError, match="twice"):
        pitch.calibrate(GAA, 140, 85, [{"landmark": "halfway.near", "u": 0, "v": 0}] * 4)


def test_attacking_direction_swaps_each_period():
    assert pitch.attacking_right("home", 1, "right") and not pitch.attacking_right("away", 1, "right")
    assert not pitch.attacking_right("home", 2, "right") and pitch.attacking_right("away", 2, "right")
    assert pitch.attacking_right("home", 2, "left")


@pytest.mark.parametrize("x,y,right,expected", [
    (130, 10, True, {"zone": "inside_45", "side": "right"}),     # 10m out, near sideline = attacker's right
    (10, 10, False, {"zone": "inside_45", "side": "left"}),      # same spot, attacking the other way
    (70, 42.5, True, {"zone": "middle_third", "side": "centre"}),
    (20, 80, True, {"zone": "own_third", "side": "left"}),
])
def test_gaa_shot_zones(x, y, right, expected):
    assert pitch.zone_attributes(GAA, "shot", x, y, 140, 85, right) == expected


def test_zone_values_respect_event_type():
    # Soccer: a shot from the six-yard box vs a turnover at the same spot (turnover has thirds only).
    assert pitch.zone_attributes(SOCCER, "shot", 103, 34, 105, 68, True) == {"zone": "six_yard_box"}
    assert pitch.zone_attributes(SOCCER, "shot", 95, 34, 105, 68, True) == {"zone": "inside_box"}
    assert pitch.zone_attributes(SOCCER, "turnover", 103, 34, 105, 68, True) == {"zone": "attacking_third"}
    assert pitch.zone_attributes(GAA, "kickout", 100, 40, 140, 85, True) == {}  # kickout has no zone/side


@pytest.mark.parametrize("pack", [GAA, SOCCER], ids=["gaa", "soccer"])
def test_pack_pitch_rules_only_use_values_the_pack_defines(pack):
    zone_values = {v for t in pack["event_types"] for a in t["attributes"] if a["id"] == "zone" for v in a["values"]}
    assert {r["value"] for r in pack["pitch"]["zones"]} <= zone_values
    side_values = {v for t in pack["event_types"] for a in t["attributes"] if a["id"] == "side" for v in a["values"]}
    assert set(pack["pitch"].get("sides", [])) <= side_values
    box_names = {b["name"] for b in pack["pitch"]["boxes"]}
    assert {r["box"] for r in pack["pitch"]["zones"] if "box" in r} <= box_names


def test_pitch_setup_api(client, match, other_club):
    m = match["id"]
    info = client.get(f"/api/matches/{m}/pitch").json()
    assert info["length"] == 140 and info["setup"] is None and len(info["landmarks"]) > 20
    chosen = [lm for lm in info["landmarks"] if lm["id"] in {"left.corner.near", "right.corner.near", "left.45m.far", "right.45m.far", "halfway.far"}]
    px = project([[lm["x"], lm["y"]] for lm in chosen])
    body = {"home_attacks_first": "right", "frame_t": 12,
            "points": [{"landmark": lm["id"], "u": u, "v": v} for lm, (u, v) in zip(chosen, px)]}
    r = client.put(f"/api/matches/{m}/pitch", json=body)
    assert r.status_code == 200 and r.json()["rms_error_m"] < 0.01
    saved = client.get(f"/api/matches/{m}").json()["pitch_setup"]
    assert saved["home_attacks_first"] == "right" and len(saved["points"]) == 5

    assert client.put(f"/api/matches/{m}/pitch", json={**body, "points": body["points"][:3]}).status_code == 422
    assert client.put(f"/api/matches/{m}/pitch", json={**body, "length": 300}).status_code == 422
    for method in ("GET", "PUT", "DELETE"):
        assert other_club.request(method, f"/api/matches/{m}/pitch", json=body).status_code == 404
    assert other_club.get(f"/api/matches/{m}/frame").status_code == 404
    assert client.delete(f"/api/matches/{m}/pitch").status_code == 204
    assert client.get(f"/api/matches/{m}").json()["pitch_setup"] is None


@needs_ffmpeg
def test_frame_endpoint(client, match, tmp_path):
    video = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=40:size=640x360:rate=10",
                    "-pix_fmt", "yuv420p", str(video)], check=True)
    upload(client, match["id"], data=video.read_bytes())
    r = client.get(f"/api/matches/{match['id']}/frame", params={"t": 12.5})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:2] == b"\xff\xd8"
