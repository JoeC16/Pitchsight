import shutil
import subprocess
from pathlib import Path

import pytest

from app import dataset
from app.db import SessionLocal
from app.models import Match

FIX = Path(__file__).parent / "fixtures"
pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def make_video(path: Path, seconds: int, *extra: str) -> Path:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=64x48:rate=2",
                    *extra, str(path)], check=True)
    return path


@pytest.fixture()
def games(tmp_path):
    (tmp_path / "tags").mkdir()
    xml = (FIX / "sportscode_sample.xml").read_text()   # tags up to 450s
    for g in ("g1", "g2", "g3"):
        (tmp_path / "tags" / f"{g}.xml").write_text(xml)
    make_video(tmp_path / "long.mp4", 600, "-pix_fmt", "yuv420p")
    make_video(tmp_path / "short.mp4", 120, "-pix_fmt", "yuv420p")   # tags run past its end
    make_video(tmp_path / "old.mkv", 600, "-c:v", "mpeg4")             # needs a playback proxy
    rows = [
        {"game_id": "g1", "video": "long.mp4", "tags": "tags/g1.xml", "home_team": "St Marys", "away_team": "Ballyboden",
         "played_on": "2026-09-14", "second_half_s": "200"},
        {"game_id": "g2", "video": "short.mp4", "tags": "tags/g2.xml"},
        {"game_id": "g3", "video": "old.mkv", "tags": "tags/g3.xml", "title": "League R3"},
    ]
    m = tmp_path / "manifest.csv"
    dataset.save_manifest(rows, m)
    return m


def load(manifest, club_id, *extra):
    return dataset.main(["load", str(manifest), "--club-id", str(club_id), "--mapping", str(FIX / "provider_mapping.yaml"), *extra])


def test_dry_run_checks_without_loading(client, games, capsys):
    before = len(client.get("/api/matches").json())
    assert load(games, client.me["club"]["id"], "--dry-run") == 0
    out = capsys.readouterr().out
    assert "would load 2" in out and "g2: not loaded" in out and "after the end of the 2.0 min video" in out
    assert len(client.get("/api/matches").json()) == before


def test_load_creates_browsable_matches_with_imported_tags(client, games, capsys):
    club = client.me["club"]["id"]
    assert load(games, club, "--complete") == 0
    out = capsys.readouterr().out
    assert "loaded 2, already loaded 0, not loaded 1, warnings 2" in out  # warnings: the unmapped "Water break"

    matches = {m["title"]: m for m in client.get("/api/matches").json()}
    g1 = matches["St Marys v Ballyboden"]
    assert g1["status"] == "ready" and g1["detector"] == "imported" and g1["played_on"] == "2026-09-14"
    assert g1["duration_s"] == pytest.approx(600, abs=1) and g1["has_video"]
    events = client.get(f"/api/matches/{g1['id']}/events").json()
    assert len(events) == 5 and all(e["origin"] == "imported" and e["reviewed"] and e["confidence"] is None for e in events)
    assert [e["period"] for e in events] == [1, 1, 1, 2, 2]  # second half starts at 200s
    assert client.get(f"/api/matches/{g1['id']}/verified-spans").json()[0]["t_end"] == pytest.approx(600, abs=1)
    # Clips work from the loaded video; the old mkv got a playback proxy.
    assert client.get(f"/api/events/{events[0]['id']}/clip").status_code == 200
    assert matches["League R3"]["proxy_status"] == "ready"

    # Re-running is safe: nothing is loaded twice.
    assert load(games, club) == 0
    assert "loaded 0, already loaded 2" in capsys.readouterr().out
    assert sum(1 for m in client.get("/api/matches").json() if m["title"] == "League R3") == 1


def test_loaded_games_are_only_in_that_club(client, other_club, games):
    load(games, client.me["club"]["id"])
    with SessionLocal() as db:
        ids = [m.id for m in db.query(Match).filter(Match.external_id == "g1").all()]
    assert ids and all(other_club.get(f"/api/matches/{i}").status_code == 404 for i in ids)


def test_unknown_club_rejected(games, capsys):
    assert load(games, 99999) == 2
    assert "no club" in capsys.readouterr().err


def test_load_into_bucket(client, games, monkeypatch, capsys):
    moto = pytest.importorskip("moto")
    from app import storage
    for k, v in {"PITCHSIGHT_STORAGE": "s3", "PITCHSIGHT_S3_BUCKET": "load-test", "AWS_ACCESS_KEY_ID": "t",
                 "AWS_SECRET_ACCESS_KEY": "t", "AWS_DEFAULT_REGION": "us-east-1"}.items():
        monkeypatch.setenv(k, v)
    storage.remote.cache_clear()
    try:
        with moto.mock_aws():
            storage.remote().client.create_bucket(Bucket="load-test")
            from fastapi.testclient import TestClient

            from app.main import app
            with TestClient(app) as c:
                c.post("/api/auth/register", json={"club_name": "Bucket club", "email": "bucket@example.com", "password": "longenough"})
                club = c.get("/api/auth/me").json()["club"]["id"]
                assert load(games, club, "--skip-proxies") == 0
                m = next(x for x in c.get("/api/matches").json() if x["title"] == "St Marys v Ballyboden")
                with SessionLocal() as db:
                    row = db.get(Match, m["id"])
                    assert row.video_key == f"videos/m{m['id']}.mp4" and row.video_path is None
                ev = c.get(f"/api/matches/{m['id']}/events").json()[0]
                assert c.get(f"/api/events/{ev['id']}/clip").status_code == 200   # fetched from the bucket
                assert c.get(f"/api/matches/{m['id']}/video", follow_redirects=False).status_code == 307
    finally:
        storage.remote.cache_clear()
