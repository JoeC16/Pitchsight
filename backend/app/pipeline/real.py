"""Real detection pipeline: tracking -> possession -> rule-based events.

Stages (see docs/ARCHITECTURE.md):

  1. Decode      frames at a reduced rate                         NOT BUILT (needs footage + model)
  2. Calibrate   pixels -> pitch metres, from the coach's pitch setup        built: pitch.py
  3. Detect      players, goalkeepers/referees, ball (e.g. RF-DETR)          NOT BUILT
  4. Track       stable ids across frames (e.g. ByteTrack)                   NOT BUILT
  5. Teams       kit-colour clustering -> home/away                          built: teams.py
  6. Possession  ball-to-player proximity + ball speed                       built: possession.py
  7. Events      kickouts, shots, turnovers with confidence                  built: rules.py
  8. Classify    trained outcome classifier (goal/point/wide/saved)          NOT BUILT (needs labels)

Until stages 1, 3 and 4 exist, tracking can be supplied as `<video file>.tracking.json`
(format in tracking.py), e.g. from an external tracker. Heavy dependencies (torch etc.) belong
in a separate GPU worker image, not in requirements.txt.
"""
from pathlib import Path

from . import rules
from .interfaces import EventDraft, MatchContext, ProgressFn, VideoInfo
from .tracking import load_json


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
        if not tracking_file.exists():
            raise DetectionUnavailable(
                "Player and ball detection isn't built yet, and no tracking file was supplied "
                f"({tracking_file.name}). Use PITCHSIGHT_DETECTOR=stub for demo data.")
        tracking = load_json(tracking_file)
        progress(0.5)
        drafts = rules.detect_events(tracking, pack, setup,
                                     period_finder(context.period_starts, info.duration_s, pack["periods"]))
        progress(1.0)
        return drafts
