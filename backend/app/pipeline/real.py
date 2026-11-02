"""Real detection pipeline: tracking -> possession -> rule-based events.

Stages (see docs/ARCHITECTURE.md):

  1. Decode      frames at a reduced rate                                    built: video.py
  2. Calibrate   pixels -> pitch metres, from the coach's pitch setup        built: pitch.py
  3. Detect      players, goalkeepers/referees, ball (e.g. RF-DETR)          seam built: detection.py;
                 no real model yet (only a detector for synthetic test videos)
  4. Track       stable ids across frames                                    simple tracker: track.py
  5. Teams       kit-colour clustering -> home/away                          built: teams.py
  6. Possession  ball-to-player proximity + ball speed                       built: possession.py
  7. Events      kickouts, shots, turnovers with confidence                  built: rules.py
  8. Classify    trained outcome classifier (goal/point/wide/saved)          NOT BUILT (needs labels)

video_pipeline.py chains 1-5 into the Tracking the rules consume. Tracking can also be supplied
as `<video file>.tracking.json` (format in tracking.py), e.g. from an external tracker. Heavy dependencies (torch etc.) belong
in a separate GPU worker image, not in requirements.txt.
"""
from pathlib import Path

from .. import config
from . import rules
from .detection import DetectorUnavailable, get_frame_detector
from .interfaces import EventDraft, MatchContext, ProgressFn, VideoInfo
from .tracking import load_json
from .video_pipeline import build_tracking


class DetectionUnavailable(RuntimeError):
    pass


def period_finder(starts: list[float] | None, duration: float, periods: int):
    """Map a video time to a period. Without known kick-off times, split the video evenly."""
    if not starts:
        starts = [duration * i / periods for i in range(periods)]
    return lambda t: max(1, sum(1 for s in starts if t >= s))


class TrackingDetector:
    name = "ai"

    def detect(self, video_path: Path, info: VideoInfo, pack: dict, progress: ProgressFn,
               context: MatchContext | None = None) -> list[EventDraft]:
        setup = context.pitch_setup if context else None
        if not setup:
            raise DetectionUnavailable("Set up the pitch (calibration and direction of play) before running detection")
        tracking_file = Path(f"{video_path}.tracking.json")
        if tracking_file.exists():  # supplied by an external tracker
            tracking = load_json(tracking_file)
        else:
            try:
                frame_detector = get_frame_detector(config.FRAME_DETECTOR)
            except DetectorUnavailable as exc:
                raise DetectionUnavailable(str(exc)) from None
            try:
                tracking = build_tracking(video_path, info, setup, frame_detector, fps=config.DETECT_FPS,
                                          progress=lambda p: progress(0.9 * p))
            except ValueError as exc:
                raise DetectionUnavailable(str(exc)) from None
        progress(0.9)
        drafts = rules.detect_events(tracking, pack, setup,
                                     period_finder(context.period_starts, info.duration_s, pack["periods"]))
        progress(1.0)
        return drafts
