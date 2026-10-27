import shutil
import subprocess

import pytest

from app import clips, config
from app.reels import merge_windows
from tests.test_api import fake_probe, upload  # noqa: F401

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def test_merge_windows():
    assert merge_windows([(10, 20), (0, 5), (18, 25), (25, 30), (40, 41)]) == [(0, 5), (10, 30), (40, 41)]
    assert merge_windows([]) == []


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    path = tmp_path_factory.mktemp("v") / "match.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=90:size=160x120:rate=10",
         "-f", "lavfi", "-i", "sine=duration=90", "-pix_fmt", "yuv420p", "-shortest", str(path)],
        check=True,
    )
    return path


def duration(path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out)


@needs_ffmpeg
def test_export_reel_end_to_end(client, match, video):
    assert upload(client, match["id"], data=video.read_bytes()).status_code == 200
    m = client.get(f"/api/matches/{match['id']}").json()
    params = {"type": "shot", "team": "home"}
    events = client.get(f"/api/matches/{match['id']}/events", params=params).json()
    assert events

    r = client.post(f"/api/matches/{match['id']}/reels", params=params)
    assert r.status_code == 202
    reel = r.json()
    assert reel["event_count"] == len(events) and reel["filters"] == "type=shot&team=home"

    # TestClient runs background tasks before returning, so the job has finished.
    reel = client.get(f"/api/reels/{reel['id']}").json()
    assert reel["status"] == "ready", reel["error"]
    windows = merge_windows([
        (max(0.0, e["t_start"] - config.CLIP_PRE_S), min(e["t_end"] + config.CLIP_POST_S, m["duration_s"]))
        for e in events
    ])
    expected = sum(e - s for s, e in windows)
    assert reel["segment_count"] == len(windows) and reel["duration_s"] == pytest.approx(expected, abs=0.05)

    dl = client.get(f"/api/reels/{reel['id']}/download")
    assert dl.status_code == 200 and dl.headers["content-type"] == "video/mp4"
    assert "DEMO-simulated-events" in dl.headers["content-disposition"]  # stub data is labelled
    out = config.DATA_DIR / "downloaded_reel.mp4"
    out.write_bytes(dl.content)
    # Each segment can be up to a frame/audio packet longer than asked for.
    assert duration(out) == pytest.approx(expected, abs=0.25 * len(windows) + 0.5)

    assert any(x["id"] == reel["id"] for x in client.get(f"/api/matches/{match['id']}/reels").json())
    assert client.delete(f"/api/reels/{reel['id']}").status_code == 204
    assert client.get(f"/api/reels/{reel['id']}").status_code == 404


def test_reel_validation(client, match, fake_probe):  # noqa: F811
    base = f"/api/matches/{match['id']}/reels"
    assert client.post(base).status_code == 409  # no video yet
    upload(client, match["id"])
    assert client.post(base, params={"type": "shot", "attr.outcome": "nonexistent"}).status_code == 422
    assert client.post(base, params={"order": "bogus"}).status_code == 422


def test_reel_too_many_events(client, match, fake_probe, monkeypatch):  # noqa: F811
    upload(client, match["id"])
    monkeypatch.setattr(config, "MAX_REEL_EVENTS", 5)
    r = client.post(f"/api/matches/{match['id']}/reels")
    assert r.status_code == 422 and "at most 5" in r.json()["detail"]


def test_failed_reel_reports_error(client, match, fake_probe, monkeypatch):  # noqa: F811
    upload(client, match["id"])  # junk bytes: cutting will fail

    def boom(*a, **k):
        raise clips.ClipError("ffmpeg failed: simulated")
    monkeypatch.setattr(clips, "get_or_cut_window", boom)
    reel = client.post(f"/api/matches/{match['id']}/reels", params={"type": "kickout"}).json()
    reel = client.get(f"/api/reels/{reel['id']}").json()
    assert reel["status"] == "failed" and "simulated" in reel["error"]
    assert client.get(f"/api/reels/{reel['id']}/download").status_code == 409


def test_reels_are_club_scoped(client, other_club, match, fake_probe, monkeypatch):  # noqa: F811
    upload(client, match["id"])
    monkeypatch.setattr(clips, "get_or_cut_window", lambda *a: (_ for _ in ()).throw(clips.ClipError("x")))
    reel = client.post(f"/api/matches/{match['id']}/reels", params={"type": "free"}).json()
    assert other_club.post(f"/api/matches/{match['id']}/reels").status_code == 404
    assert other_club.get(f"/api/matches/{match['id']}/reels").status_code == 404
    for method, path in [("GET", f"/api/reels/{reel['id']}"), ("GET", f"/api/reels/{reel['id']}/download"),
                         ("DELETE", f"/api/reels/{reel['id']}")]:
        assert other_club.request(method, path).status_code == 404


def test_deleting_match_removes_reels(client, match, fake_probe, monkeypatch):  # noqa: F811
    from app.db import SessionLocal
    from app.models import Reel

    upload(client, match["id"])
    monkeypatch.setattr(clips, "get_or_cut_window", lambda *a: (_ for _ in ()).throw(clips.ClipError("x")))
    reel = client.post(f"/api/matches/{match['id']}/reels", params={"type": "free"}).json()
    client.delete(f"/api/matches/{match['id']}")
    with SessionLocal() as db:
        assert db.get(Reel, reel["id"]) is None
