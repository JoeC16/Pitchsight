"""Direct-to-bucket multipart uploads, against moto's S3."""
from urllib.parse import parse_qs, urlparse

import pytest

from app import config, storage
from tests.test_api import fake_probe  # noqa: F401

moto = pytest.importorskip("moto")
BUCKET = "direct-test"


@pytest.fixture()
def s3(monkeypatch):
    for k, v in {"PITCHSIGHT_STORAGE": "s3", "PITCHSIGHT_S3_BUCKET": BUCKET, "AWS_ACCESS_KEY_ID": "t",
                 "AWS_SECRET_ACCESS_KEY": "t", "AWS_DEFAULT_REGION": "us-east-1"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("PITCHSIGHT_S3_PREFIX", raising=False)
    monkeypatch.delenv("PITCHSIGHT_S3_ENDPOINT", raising=False)
    storage.remote.cache_clear()
    with moto.mock_aws():
        store = storage.remote()
        store.client.create_bucket(Bucket=BUCKET)
        yield store
    storage.remote.cache_clear()


def browser_upload(client, store, m, data: bytes, filename="game.mp4"):
    """What the browser does: start, get signed URLs, PUT each part, complete."""
    r = client.post(f"/api/matches/{m}/uploads", json={"filename": filename, "size": len(data)})
    assert r.status_code == 201, r.text
    up = r.json()
    nums = list(range(1, up["part_count"] + 1))
    urls = client.post(f"/api/matches/{m}/uploads/{up['upload_id']}/parts", params={"filename": filename},
                       json={"part_numbers": nums}).json()["urls"]
    parts = []
    for n in nums:
        q = parse_qs(urlparse(urls[str(n)]).query)
        assert q["uploadId"] == [up["upload_id"]] and q["partNumber"] == [str(n)]
        # The browser PUTs to the signed URL; here the S3 client sends the same part.
        chunk = data[(n - 1) * up["part_size"]: n * up["part_size"]]
        etag = store.client.upload_part(Bucket=BUCKET, Key=f"videos/m{m}.mp4", UploadId=up["upload_id"],
                                        PartNumber=n, Body=chunk)["ETag"]
        parts.append({"part_number": n, "etag": etag})
    return up, parts


def test_multipart_upload_then_processing(client, match, s3, fake_probe, monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "UPLOAD_PART_BYTES", 5 * 2**20)  # S3's minimum, so 12 MB -> 3 parts
    m = match["id"]
    data = bytes(range(256)) * (12 * 2**20 // 256)
    up, parts = browser_upload(client, s3, m, data)
    assert up["part_count"] == 3
    r = client.post(f"/api/matches/{m}/uploads/{up['upload_id']}/complete", json={"filename": "game.mp4", "parts": parts})
    assert r.status_code == 200
    info = client.get(f"/api/matches/{m}").json()
    assert info["status"] == "ready" and info["has_video"]           # processed from the bucket copy
    obj = s3.client.get_object(Bucket=BUCKET, Key=f"videos/m{m}.mp4")
    assert obj["ContentLength"] == len(data) and obj["ContentType"] == "video/mp4"
    assert client.get(f"/api/matches/{m}/video", follow_redirects=False).status_code == 307
    # Uploading again is refused once the match has a video.
    assert client.post(f"/api/matches/{m}/uploads", json={"filename": "x.mp4", "size": 10}).status_code == 409


def test_part_size_respects_s3_part_limit(client, match, s3):
    big = 2 * 1024**4  # 2 TB: 64 MB parts would need 32,768 parts
    config_max = config.MAX_UPLOAD_BYTES
    try:
        config.MAX_UPLOAD_BYTES = big
        up = client.post(f"/api/matches/{match['id']}/uploads", json={"filename": "g.mp4", "size": big}).json()
    finally:
        config.MAX_UPLOAD_BYTES = config_max
    assert up["part_count"] <= 10000 and up["part_size"] * up["part_count"] >= big


def test_validation_and_abort(client, match, s3):
    m = match["id"]
    assert client.post(f"/api/matches/{m}/uploads", json={"filename": "notes.txt", "size": 10}).status_code == 415
    assert client.post(f"/api/matches/{m}/uploads",
                       json={"filename": "g.mp4", "size": config.MAX_UPLOAD_BYTES + 1}).status_code == 413
    up = client.post(f"/api/matches/{m}/uploads", json={"filename": "g.mp4", "size": 100}).json()
    bad = client.post(f"/api/matches/{m}/uploads/{up['upload_id']}/complete",
                      json={"filename": "g.mp4", "parts": [{"part_number": 1, "etag": '"nope"'}]})
    assert bad.status_code == 400
    assert client.delete(f"/api/matches/{m}/uploads/{up['upload_id']}", params={"filename": "g.mp4"}).status_code == 204
    assert s3.client.list_multipart_uploads(Bucket=BUCKET).get("Uploads", []) == []
    assert client.get(f"/api/matches/{m}").json()["status"] == "created"


def test_local_storage_says_use_put(client, match):
    storage.remote.cache_clear()
    r = client.post(f"/api/matches/{match['id']}/uploads", json={"filename": "g.mp4", "size": 10})
    assert r.status_code == 409 and "PUT" in r.json()["detail"]


def test_other_club_cannot_upload(client, other_club, match, s3):
    up = client.post(f"/api/matches/{match['id']}/uploads", json={"filename": "g.mp4", "size": 100}).json()
    for method, path, body in [("POST", f"/api/matches/{match['id']}/uploads", {"filename": "g.mp4", "size": 1}),
                               ("POST", f"/api/matches/{match['id']}/uploads/{up['upload_id']}/parts", {"part_numbers": [1]}),
                               ("POST", f"/api/matches/{match['id']}/uploads/{up['upload_id']}/complete",
                                {"filename": "g.mp4", "parts": [{"part_number": 1, "etag": "x"}]}),
                               ("DELETE", f"/api/matches/{match['id']}/uploads/{up['upload_id']}", None)]:
        assert other_club.request(method, path, json=body).status_code == 404


def test_bucket_cors_cli(s3):
    from app.cli import main
    main(["bucket-cors", "https://app.example.com"])
    rule = s3.client.get_bucket_cors(Bucket=BUCKET)["CORSRules"][0]
    assert rule["AllowedOrigins"] == ["https://app.example.com"] and "ETag" in rule["ExposeHeaders"] and "PUT" in rule["AllowedMethods"]
