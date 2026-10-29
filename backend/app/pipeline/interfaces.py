"""The contract between video processing and the rest of the app.

A Detector turns a video file into event drafts. Everything downstream (database,
API, UI, clips) only depends on this interface, so the stub can be swapped for a real
computer-vision pipeline without touching anything else.
"""
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class VideoInfo:
    duration_s: float
    fps: float | None = None
    width: int | None = None
    height: int | None = None
    video_codec: str | None = None   # ffprobe codec_name, e.g. "h264", "hevc"
    pix_fmt: str | None = None       # e.g. "yuv420p"
    audio_codec: str | None = None   # None when there is no audio stream
    bit_rate: int | None = None      # bits/s, whole file
    max_keyframe_gap_s: float | None = None


@dataclass
class EventDraft:
    type: str                       # must be an event type in the sport pack
    team: str | None                # "home" | "away" | None (see team_means in the pack)
    period: int
    t_start: float                  # seconds into the video
    t_end: float
    attributes: dict[str, str] = field(default_factory=dict)
    confidence: float | None = None  # 0..1


ProgressFn = Callable[[float], None]  # call with 0..1 as work completes


@dataclass
class MatchContext:
    """What a detector may need to know about the match beyond the video itself."""

    sport: str
    pitch_setup: dict | None = None   # Match.pitch_setup: calibration + direction of play (pitch.py)
    period_starts: list[float] | None = None  # video time each period kicks off, if known


class Detector(Protocol):
    name: str  # stored as Event.origin, e.g. "stub" or "ai"

    def detect(
        self, video_path: Path, info: VideoInfo, pack: dict, progress: ProgressFn,
        context: MatchContext | None = None,
    ) -> list[EventDraft]: ...
