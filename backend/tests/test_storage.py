"""Object storage (S3-compatible), against moto's in-memory S3."""
import shutil
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from app import storage
from tests.test_api import upload

moto = pytest.importorskip("moto")
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
BUCKET = "pitchsight-test"


@pytest.fixture()
def s3(monkeypatch):
    for k, v in {"PITCHSIGHT_STORAGE": "s3", "PITCHSIGHT_S3_BUCKET": BUCKET, "PITCHSIGHT_S3_PREFIX": "club-data",
                 "AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test", "AWS_DEFAULT_REGION": "eu-west-1"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("PITCHSIGHT_S3_ENDPOINT", raising=False)
    storage.remote.cache_clear()
    with moto.mock_aws():
        store = storage.remote()
        store.client.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-west-1"})
        yield store
    storage.remote.cache_clear()


def keys(store) -> set[str]:
    return {o["Key"] for o in store.client.list_objects_v2(Bucket=BUCKET).get("Contents", [])}


def test_local_is_default_and_bad_config_is_rejected(monkeypatch):
    storage.remote.cache_clear()
    monkeypatch.delenv("PITCHSIGHT_STORAGE", raising=False)
    assert storage.remote() is None and storage.persist(Path("/x"), "k") is None and storage.signed_url("k") is None
    storage.remote.cache_clear()
    monkeypatch.setenv("PITCHSIGHT_STORAGE", "s3")
    monkeypatch.delenv("PITCHSIGHT_S3_BUCKET", raising=False)
    with pytest.raises(ValueError, match="BUCKET"):
        storage.remote()
    storage.remote.cache_clear()


def test_put_get_round_trip_and_cache(s3, tmp_path):
    src = tmp_path / "a.bin"
    src.write_bytes(b"x" * 1000)
    assert storage.persist(src, "videos/a.bin") == "videos/a.bin"
    assert keys(s3) == {"club-data/videos/a.bin"}
    got = storage.ensure_local(tmp_path / "not-here" / "a.bin", "videos/a.bin")
    assert got == storage.cache_path("videos/a.bin") and got.read_bytes() == src.read_bytes()
    assert not list(got.parent.glob("*.download"))  # no partial file left behind
    assert storage.ensure_local(src, "videos/a.bin") == src  # a local original wins
    with pytest.raises(FileNotFoundError):
        storage.ensure_local(tmp_path / "nope", None)
    url = storage.signed_url("videos/a.bin", filename="game.mp4", content_type="video/mp4")
    q = parse_qs(urlparse(url).query)
    assert "club-data/videos/a.bin" in url and q["response-content-disposition"] == ['attachment; filename="game.mp4"']


@needs_ffmpeg
def test_match_lifecycle_with_bucket(s3, client, match, tmp_path):
    video = tmp_path / "game.mkv"  # mkv -> also gets a proxy, which must be stored too
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=60:size=160x120:rate=10",
                    "-c:v", "mpeg4", str(video)], check=True)
    m = match["id"]
    assert upload(client, m, data=video.read_bytes(), name="game.mkv").status_code == 200
    info = client.get(f"/api/matches/{m}").json()
    assert info["status"] == "ready" and info["proxy_status"] == "ready"
    assert {f"club-data/videos/m{m}.mkv", f"club-data/proxies/m{m}.mp4"} <= keys(s3)

    # Playback redirects to a signed URL for the proxy instead of streaming through the app.
    r = client.get(f"/api/matches/{m}/video", follow_redirects=False)
    assert r.status_code == 307 and f"club-data/proxies/m{m}.mp4" in r.headers["location"]

    # As on another machine: the recorded path points at a disk this machine doesn't have.
    # Clips and frames still work, from this machine's own cache, filled from the bucket.
    from app import config
    from app.db import SessionLocal
    from app.models import Match
    with SessionLocal() as db:
        row = db.get(Match, m)
        Path(row.video_path).unlink()
        row.video_path = f"/elsewhere/machine-a/videos/{m}.mkv"
        db.commit()
    ev = client.get(f"/api/events/{client.get(f'/api/matches/{m}/events').json()[0]['id']}")
    assert client.get(f"/api/events/{ev.json()['id']}/clip").status_code == 200
    cached = config.DATA_DIR / "cache" / f"videos/m{m}.mkv"
    assert cached.exists() and not Path("/elsewhere").exists()
    assert client.get(f"/api/matches/{m}/frame", params={"t": 5}).status_code == 200

    # Reels are stored too and downloaded via a signed URL with the right filename.
    reel = client.post(f"/api/matches/{m}/reels", params={"type": "shot"}).json()
    assert client.get(f"/api/reels/{reel['id']}").json()["status"] == "ready"
    dl = client.get(f"/api/reels/{reel['id']}/download", follow_redirects=False)
    assert dl.status_code == 307 and "reel" in parse_qs(urlparse(dl.headers["location"]).query)["response-content-disposition"][0]
    assert any(k.startswith("club-data/reels/") for k in keys(s3))

    # Deleting the match removes everything from the bucket.
    assert client.delete(f"/api/matches/{m}").status_code == 204
    assert not [k for k in keys(s3) if f"m{m}" in k]
