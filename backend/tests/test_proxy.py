import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import config
from app.pipeline.interfaces import VideoInfo
from app.pipeline.probe import probe
from app.proxy import needs_proxy
from tests.test_api import upload

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")

GOOD = dict(duration_s=600, fps=25, width=1920, height=1080, video_codec="h264", pix_fmt="yuv420p",
            audio_codec="aac", bit_rate=8_000_000, max_keyframe_gap_s=2.0)


@pytest.mark.parametrize("change,path,reason", [
    ({}, "m.mp4", None),
    ({"audio_codec": None}, "m.mov", None),
    ({}, "m.mkv", ".mkv files"),
    ({"video_codec": "hevc"}, "m.mp4", "hevc video"),
    ({"pix_fmt": "yuv422p10le"}, "m.mp4", "yuv422p10le pixel format"),
    ({"audio_codec": "pcm_s16le"}, "m.mov", "pcm_s16le audio"),
    ({"width": 3840, "height": 2160}, "m.mp4", "3840x2160 is larger than 1080p"),
    ({"bit_rate": 45_000_000}, "m.mp4", "bitrate 45 Mbit/s"),
    ({"max_keyframe_gap_s": 30.0}, "m.mp4", "keyframes are 30s apart"),
])
def test_needs_proxy(change, path, reason):
    got = needs_proxy(VideoInfo(**{**GOOD, **change}), Path(path))
    assert (got is None) if reason is None else (reason in got)


def test_proxy_mode_override(monkeypatch):
    monkeypatch.setattr(config, "PROXY_MODE", "never")
    assert needs_proxy(VideoInfo(**{**GOOD, "video_codec": "hevc"}), Path("m.mkv")) is None
    monkeypatch.setattr(config, "PROXY_MODE", "always")
    assert needs_proxy(VideoInfo(**GOOD), Path("m.mp4"))


def make_video(path: Path, *extra: str) -> Path:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=40:size=320x240:rate=10",
                    "-f", "lavfi", "-i", "sine=duration=40", "-shortest", *extra, str(path)], check=True)
    return path


def codec_of(data: bytes, tmp: Path) -> str:
    f = tmp / "served.bin"
    f.write_bytes(data)
    return probe(f).video_codec


@needs_ffmpeg
def test_probe_reports_codec_and_keyframes(tmp_path):
    info = probe(make_video(tmp_path / "a.mp4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "50", "-c:a", "aac"))
    assert (info.video_codec, info.pix_fmt, info.audio_codec, info.width) == ("h264", "yuv420p", "aac", 320)
    assert info.max_keyframe_gap_s == pytest.approx(5.0, abs=0.2)  # -g 50 at 10 fps


@needs_ffmpeg
def test_unfriendly_upload_gets_a_proxy(client, match, tmp_path):
    src = make_video(tmp_path / "game.mkv", "-c:v", "mpeg4", "-c:a", "libopus")
    assert upload(client, match["id"], data=src.read_bytes(), name="game.mkv").status_code == 200
    m = client.get(f"/api/matches/{match['id']}").json()
    assert m["status"] == "ready" and m["video_codec"] == "mpeg4"
    assert m["proxy_status"] == "ready" and ".mkv" in m["proxy_reason"]
    served = client.get(f"/api/matches/{match['id']}/video")
    assert served.headers["content-type"] == "video/mp4" and codec_of(served.content, tmp_path) == "h264"
    # Range requests still work on the proxy.
    assert client.get(f"/api/matches/{match['id']}/video", headers={"Range": "bytes=0-99"}).status_code == 206
    # Clips are still cut from the original.
    ev = client.get(f"/api/matches/{match['id']}/events").json()[0]
    assert client.get(f"/api/events/{ev['id']}/clip").status_code == 200

    proxy = config.PROXY_DIR / f"m{match['id']}.mp4"
    assert proxy.exists()
    client.delete(f"/api/matches/{match['id']}")
    assert not proxy.exists()


@needs_ffmpeg
def test_friendly_upload_is_served_as_is(client, match, tmp_path):
    src = make_video(tmp_path / "game.mp4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "20", "-c:a", "aac")
    upload(client, match["id"], data=src.read_bytes())
    m = client.get(f"/api/matches/{match['id']}").json()
    assert m["proxy_status"] == "none" and m["proxy_reason"] is None
    assert client.get(f"/api/matches/{match['id']}/video").content == src.read_bytes()


@needs_ffmpeg
def test_failed_proxy_falls_back_to_original(client, match, tmp_path, monkeypatch):
    from app import proxy
    src = make_video(tmp_path / "game.mkv", "-c:v", "mpeg4")
    def broken_ffmpeg(*a, **k):
        raise OSError("simulated ffmpeg crash")
    monkeypatch.setattr(proxy, "subprocess", SimpleNamespace(Popen=broken_ffmpeg, PIPE=subprocess.PIPE))
    upload(client, match["id"], data=src.read_bytes(), name="game.mkv")
    m = client.get(f"/api/matches/{match['id']}").json()
    assert m["status"] == "ready" and m["proxy_status"] == "failed"
    assert client.get(f"/api/matches/{match['id']}/video").content == src.read_bytes()
