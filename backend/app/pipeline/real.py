"""Real detection pipeline: NOT IMPLEMENTED YET. This is the skeleton to build into.

Target stages (see docs/ROADMAP.md for the reasoning and exit criteria):

  1. Decode      stream frames at a reduced rate (e.g. 5-10 fps) with ffmpeg/PyAV
  2. Calibrate   Veo's panoramic camera is fixed, so calibrate once per match:
                 detect pitch keypoints/lines -> homography to pitch coordinates
                 (GAA pitch markings: goals, 13m, 20m, 45m, 65m lines, halfway)
  3. Detect      players, goalkeepers/referees, ball (YOLO-family / RF-DETR; fine-tune
                 on Veo-style footage because the ball is only a few pixels wide)
  4. Track       multi-object tracking (e.g. ByteTrack) -> stable ids
  5. Teams       cluster kit colours -> home/away assignment
  6. Possession  ball-to-player proximity + ball velocity -> possession timeline
  7. Events      rules over tracking data (turnover = possession change; kickout =
                 restart from own goal area; shot = ball launched toward goal) and a
                 video-action model for what tracking cannot see
  8. Classify    attributes (outcome, zone, side, method...) from tracking + a trained
                 outcome classifier; emit confidence so low-confidence items go to review

Implement `TrackingDetector.detect` returning EventDrafts that validate against the sport
pack, then set PITCHSIGHT_DETECTOR=real. Heavy dependencies (torch etc.) belong in a
separate requirements file / GPU worker image, not in requirements.txt.
"""
from pathlib import Path

from .interfaces import EventDraft, ProgressFn, VideoInfo


class TrackingDetector:
    name = "ai"

    def detect(self, video_path: Path, info: VideoInfo, pack: dict, progress: ProgressFn) -> list[EventDraft]:
        raise NotImplementedError(
            "The real detection pipeline is not built yet. Use PITCHSIGHT_DETECTOR=stub for demo data."
        )
