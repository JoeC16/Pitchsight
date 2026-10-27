import shutil
import subprocess

import pytest

from app.pipeline import runner
from app.pipeline.interfaces import VideoInfo


@pytest.fixture()
def fake_probe(monkeypatch):
    """Uploads in most tests are junk bytes; pretend ffprobe saw a 70-minute video."""
    monkeypatch.setattr(runner, "probe", lambda path: VideoInfo(duration_s=4200.0, fps=25.0))


def upload(client, match_id, data=b"not-really-a-video", name="game.mp4"):
    return client.put(f"/api/matches/{match_id}/video", content=data, headers={"X-Filename": name})


def test_sports_listed_and_taxonomy_served(client):
    sports = {s["sport"] for s in client.get("/api/sports").json()}
    assert {"gaa_football", "soccer"} <= sports
    pack = client.get("/api/sports/gaa_football").json()
    assert any(t["id"] == "kickout" for t in pack["event_types"])
    assert "_types" not in pack
    assert client.get("/api/sports/hurling").status_code == 404


def test_create_match_rejects_unknown_sport(client):
    r = client.post("/api/matches", json={"title": "x", "sport": "curling", "home_team": "a", "away_team": "b"})
    assert r.status_code == 422


def test_upload_processes_and_creates_events(client, match, fake_probe):
    r = upload(client, match["id"])
    assert r.status_code == 200
    m = client.get(f"/api/matches/{match['id']}").json()
    assert m["status"] == "ready" and m["detector"] == "stub" and m["duration_s"] == 4200.0
    events = client.get(f"/api/matches/{match['id']}/events").json()
    assert len(events) > 50
    assert all(e["origin"] == "stub" for e in events)
    assert [e["t_start"] for e in events] == sorted(e["t_start"] for e in events)


def test_upload_validation(client, match, fake_probe):
    assert upload(client, match["id"], name="notes.txt").status_code == 415
    assert upload(client, match["id"], data=b"").status_code == 400
    assert upload(client, 99999).status_code == 404
    assert upload(client, match["id"]).status_code == 200
    assert upload(client, match["id"]).status_code == 409  # only once


def test_unreadable_video_marks_match_failed(client, match):
    upload(client, match["id"], data=b"definitely not a video")  # real probe runs here
    m = client.get(f"/api/matches/{match['id']}").json()
    assert m["status"] == "failed" and m["error"]


def test_filters(client, match, fake_probe):
    upload(client, match["id"])
    base = f"/api/matches/{match['id']}/events"
    total = len(client.get(base).json())

    shots = client.get(base, params={"type": "shot"}).json()
    assert shots and all(e["type"] == "shot" for e in shots)

    both = client.get(base, params={"type": "shot,turnover"}).json()
    assert {e["type"] for e in both} == {"shot", "turnover"}

    home_p2 = client.get(base, params={"team": "home", "period": 2}).json()
    assert all(e["team"] == "home" and e["period"] == 2 for e in home_p2)
    assert 0 < len(home_p2) < total

    scores = client.get(base, params={"type": "shot", "attr.outcome": "point,goal"}).json()
    assert all(e["attributes"]["outcome"] in ("point", "goal") for e in scores)

    confident = client.get(base, params={"min_conf": "0.9"}).json()
    assert all(e["confidence"] >= 0.9 for e in confident) and len(confident) < total

    assert client.get(base, params={"attr.bad key!": "x"}).status_code == 422


def test_manual_tag_validation_and_edit(client, match):
    base = f"/api/matches/{match['id']}/events"
    good = {"type": "turnover", "team": "away", "period": 1, "t_start": 10, "t_end": 14,
            "attributes": {"kind": "tackle", "zone": "middle_third"}}
    r = client.post(base, json=good)
    assert r.status_code == 201
    event = r.json()
    assert event["origin"] == "manual" and event["reviewed"] and event["confidence"] is None

    assert client.post(base, json={**good, "type": "nonsense"}).status_code == 422
    assert client.post(base, json={**good, "attributes": {"kind": "teleport"}}).status_code == 422
    assert client.post(base, json={**good, "t_end": 1}).status_code == 422

    patched = client.patch(f"/api/events/{event['id']}", json={"attributes": {"kind": "interception"}})
    assert patched.status_code == 200 and patched.json()["attributes"] == {"kind": "interception"}
    bad = client.patch(f"/api/events/{event['id']}", json={"type": "kickout"})  # old attrs invalid for kickout
    assert bad.status_code == 422

    assert client.delete(f"/api/events/{event['id']}").status_code == 204
    assert client.get(f"/api/events/{event['id']}").status_code == 404


def test_editing_an_ai_event_marks_it_reviewed(client, match, fake_probe):
    upload(client, match["id"])
    event = client.get(f"/api/matches/{match['id']}/events", params={"type": "kickout"}).json()[0]
    assert event["reviewed"] is False
    assert client.patch(f"/api/events/{event['id']}", json={"team": "home"}).json()["reviewed"] is True


def test_reprocess_keeps_manual_and_reviewed(client, match, fake_probe):
    upload(client, match["id"])
    base = f"/api/matches/{match['id']}/events"
    reviewed = client.get(base).json()[0]
    client.patch(f"/api/events/{reviewed['id']}", json={"reviewed": True})
    manual = client.post(base, json={"type": "free", "team": "home", "period": 1, "t_start": 5, "t_end": 6}).json()

    assert client.post(f"/api/matches/{match['id']}/reprocess").status_code == 200
    ids = {e["id"] for e in client.get(base).json()}
    assert reviewed["id"] in ids and manual["id"] in ids


def test_summary_and_score(client, match):
    base = f"/api/matches/{match['id']}/events"
    def shot(team, outcome):
        client.post(base, json={"type": "shot", "team": team, "period": 1, "t_start": 1, "t_end": 2,
                                "attributes": {"outcome": outcome}})
    shot("home", "goal"); shot("home", "point"); shot("home", "wide"); shot("away", "point")
    s = client.get(f"/api/matches/{match['id']}/summary").json()
    assert s["score"]["home"]["display"] == "1-01 (4)"
    assert s["score"]["away"]["display"] == "0-01 (1)"
    assert s["counts"]["shot"]["home"] == 3
    assert s["breakdown"]["shot"]["outcome"]["wide"]["home"] == 1


def test_video_range_requests(client, match, fake_probe):
    upload(client, match["id"], data=b"0123456789" * 100)
    r = client.get(f"/api/matches/{match['id']}/video", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and r.content == b"0123456789"


def test_delete_match_removes_files(client, match, fake_probe):
    upload(client, match["id"])
    path = client.get(f"/api/matches/{match['id']}").json()
    assert client.delete(f"/api/matches/{match['id']}").status_code == 204
    assert client.get(f"/api/matches/{path['id']}").status_code == 404


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_end_to_end_with_real_video_and_clip(client, match, tmp_path):
    video = tmp_path / "t.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=60:size=160x120:rate=10",
         "-pix_fmt", "yuv420p", str(video)],
        check=True,
    )
    assert upload(client, match["id"], data=video.read_bytes()).status_code == 200
    m = client.get(f"/api/matches/{match['id']}").json()
    assert m["status"] == "ready" and 59 < m["duration_s"] < 61  # real ffprobe

    event = client.get(f"/api/matches/{match['id']}/events").json()[5]
    clip = client.get(f"/api/events/{event['id']}/clip")
    assert clip.status_code == 200 and clip.headers["content-type"] == "video/mp4" and len(clip.content) > 1000


def test_review_queue_query(client, match, fake_probe):
    upload(client, match["id"])
    base = f"/api/matches/{match['id']}/events"
    first = client.get(base, params={"reviewed": "false", "order": "confidence"}).json()
    confs = [e["confidence"] for e in first]
    assert confs == sorted(confs) and not any(e["reviewed"] for e in first)

    # Confirming the head of the queue removes it; the next-least-confident is now first.
    client.patch(f"/api/events/{first[0]['id']}", json={"reviewed": True})
    after = client.get(base, params={"reviewed": "false", "order": "confidence"}).json()
    assert after[0]["id"] == first[1]["id"] and len(after) == len(first) - 1
    assert [e["id"] for e in client.get(base, params={"reviewed": "true"}).json()] == [first[0]["id"]]

    # Combines with the normal filters (review just the kickouts).
    kick = client.get(base, params={"reviewed": "false", "order": "confidence", "type": "kickout"}).json()
    assert kick and all(e["type"] == "kickout" for e in kick)

    assert client.get(base, params={"order": "random"}).status_code == 422
    assert client.get(base, params={"reviewed": "maybe"}).status_code == 422


def test_review_queue_puts_unknown_confidence_first(client, match):
    base = f"/api/matches/{match['id']}/events"
    e = client.post(base, json={"type": "free", "t_start": 5, "t_end": 6}).json()
    client.patch(f"/api/events/{e['id']}", json={"reviewed": False})  # manual tag, confidence None
    from app.db import SessionLocal
    from app.models import Event
    with SessionLocal() as db:
        db.add(Event(match_id=match["id"], type="free", period=1, t_start=1, t_end=2, attributes={},
                     confidence=0.1, origin="ai", reviewed=False))
        db.commit()
    queue = client.get(base, params={"reviewed": "false", "order": "confidence"}).json()
    assert [q["confidence"] for q in queue] == [None, 0.1]
