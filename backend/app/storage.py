"""Where durable files live: local disk (default) or an S3-compatible bucket (AWS S3,
Cloudflare R2, MinIO).

With a bucket, it holds the permanent copy of each video, proxy and reel, and local disk is
only a cache: anything that needs the file on disk (ffmpeg, detection) calls ensure_local(),
which downloads it if it isn't there, so a worker on another machine can process any match.
Browsers are redirected to short-lived signed URLs for playback and downloads.

    PITCHSIGHT_STORAGE=local | s3
    PITCHSIGHT_S3_BUCKET, PITCHSIGHT_S3_PREFIX (optional), PITCHSIGHT_S3_ENDPOINT (R2/MinIO),
    PITCHSIGHT_S3_REGION; credentials via the usual AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY.
"""
import logging
import os
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

# Signed URLs must outlive a viewing session: the browser keeps making range requests to the
# same URL while a coach scrubs through a match.
URL_EXPIRES_S = int(os.environ.get("PITCHSIGHT_S3_URL_EXPIRES_S", str(6 * 3600)))


class S3Storage:
    def __init__(self, bucket: str, prefix: str = "", endpoint_url: str | None = None, region: str | None = None):
        import boto3
        from boto3.s3.transfer import TransferConfig

        self.bucket, self.prefix = bucket, prefix.strip("/")
        self.client = boto3.client("s3", endpoint_url=endpoint_url or None, region_name=region or None)
        # Multi-GB match files: parallel multipart transfers in 64 MB parts.
        self.transfer = TransferConfig(multipart_threshold=64 * 2**20, multipart_chunksize=64 * 2**20, max_concurrency=8)

    def _k(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def put(self, local: Path, key: str) -> None:
        self.client.upload_file(str(local), self.bucket, self._k(key), Config=self.transfer)

    def get(self, key: str, local: Path) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)
        part = local.with_name(local.name + ".download")
        self.client.download_file(self.bucket, self._k(key), str(part), Config=self.transfer)
        part.replace(local)  # never leave a half-downloaded file where ffmpeg would read it

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._k(key))
            return True
        except ClientError:
            return False

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._k(key))

    # ---- multipart uploads straight from the browser (the API never sees the bytes)
    def start_multipart(self, key: str, content_type: str) -> str:
        return self.client.create_multipart_upload(Bucket=self.bucket, Key=self._k(key), ContentType=content_type)["UploadId"]

    def part_urls(self, key: str, upload_id: str, numbers: list[int]) -> dict[int, str]:
        return {n: self.client.generate_presigned_url(
            "upload_part", Params={"Bucket": self.bucket, "Key": self._k(key), "UploadId": upload_id, "PartNumber": n},
            ExpiresIn=3600) for n in numbers}

    def complete_multipart(self, key: str, upload_id: str, parts: list[tuple[int, str]]) -> int:
        """Finish the upload; returns the object's size in bytes."""
        self.client.complete_multipart_upload(
            Bucket=self.bucket, Key=self._k(key), UploadId=upload_id,
            MultipartUpload={"Parts": [{"PartNumber": n, "ETag": etag} for n, etag in sorted(parts)]})
        return self.client.head_object(Bucket=self.bucket, Key=self._k(key))["ContentLength"]

    def abort_multipart(self, key: str, upload_id: str) -> None:
        self.client.abort_multipart_upload(Bucket=self.bucket, Key=self._k(key), UploadId=upload_id)

    def set_cors(self, origins: list[str]) -> None:
        """Let browsers on these origins upload parts and read the ETag header they need."""
        self.client.put_bucket_cors(Bucket=self.bucket, CORSConfiguration={"CORSRules": [{
            "AllowedOrigins": origins, "AllowedMethods": ["PUT", "GET", "HEAD"], "AllowedHeaders": ["*"],
            "ExposeHeaders": ["ETag"], "MaxAgeSeconds": 3600}]})

    def url(self, key: str, filename: str | None = None, content_type: str | None = None) -> str:
        params = {"Bucket": self.bucket, "Key": self._k(key)}
        if filename:
            params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
        if content_type:
            params["ResponseContentType"] = content_type
        return self.client.generate_presigned_url("get_object", Params=params, ExpiresIn=URL_EXPIRES_S)


@lru_cache
def remote() -> S3Storage | None:
    """The bucket, or None when files stay on local disk."""
    kind = os.environ.get("PITCHSIGHT_STORAGE", "local")
    if kind == "local":
        return None
    if kind != "s3":
        raise ValueError(f"PITCHSIGHT_STORAGE must be 'local' or 's3', not {kind!r}")
    bucket = os.environ.get("PITCHSIGHT_S3_BUCKET")
    if not bucket:
        raise ValueError("PITCHSIGHT_STORAGE=s3 needs PITCHSIGHT_S3_BUCKET")
    return S3Storage(bucket, os.environ.get("PITCHSIGHT_S3_PREFIX", ""),
                     os.environ.get("PITCHSIGHT_S3_ENDPOINT"), os.environ.get("PITCHSIGHT_S3_REGION"))


def persist(local: Path, key: str) -> str | None:
    """Copy a finished file to the bucket. Returns the key, or None with local storage."""
    store = remote()
    if store is None:
        return None
    store.put(local, key)
    return key


def cache_path(key: str) -> Path:
    """Where this machine keeps its copy of a stored object. Derived from this machine's own
    data dir: a path recorded by another machine (e.g. the API that took the upload) means
    nothing here."""
    from . import config
    return config.DATA_DIR / "cache" / key


def ensure_local(local: str | Path | None, key: str | None) -> Path:
    """The file on local disk: the original if this machine has it, else this machine's cached
    copy, downloading it from the bucket first if needed."""
    if local and Path(local).exists():
        return Path(local)
    store = remote()
    if key and store is not None:
        cached = cache_path(key)
        if not cached.exists():
            store.get(key, cached)
        return cached
    raise FileNotFoundError(f"{local or key} is not on this machine or in storage")


def signed_url(key: str | None, filename: str | None = None, content_type: str | None = None) -> str | None:
    """A browser-usable URL for a stored object, or None (serve the local file instead)."""
    store = remote()
    return store.url(key, filename, content_type) if store is not None and key else None


def remove(*keys: str | None) -> None:
    store = remote()
    for key in keys:
        if store is not None and key:
            try:
                store.delete(key)
            except Exception:  # noqa: BLE001 - deleting a match must not fail on a storage hiccup
                log.exception("could not delete %s from storage", key)
