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
    cache = tmp_path / "cache" / "a.bin"
    assert storage.ensure_local(cache, "videos/a.bin") == cache and cache.read_bytes() == src.read_bytes()
    assert not list(cache.parent.glob("*.download"))  # no partial file left behind
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

    # Wipe the local cache (as on a fresh worker): clips and frames still work, from the bucket.
    from app.db import SessionLocal
    from app.models import Match
    with SessionLocal() as db:
        local = Path(db.get(Match, m).video_path)
    local.unlink()
    ev = client.get(f"/api/matches/{m}/events").json()[0]
    assert client.get(f"/api/events/{ev['id']}/clip").status_code == 200
    assert local.exists()  # re-downloaded into the cache
    local.unlink()
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
