"""Stub detector: generates plausible-looking SYNTHETIC events so the whole product
(timeline, filters, clips, review) can be built and demoed before real detection exists.

The events do not correspond to anything in the video. The UI flags them as demo data
via Event.origin == "stub".
"""
import random
from pathlib import Path

from .interfaces import EventDraft, ProgressFn, VideoInfo

# Rough events-per-match by type id; unknown types get DEFAULT_COUNT.
COUNTS = {"shot": 40, "turnover": 55, "kickout": 30, "free": 20, "set_piece": 35}
DEFAULT_COUNT = 15


class StubDetector:
    name = "stub"

    def detect(self, video_path: Path, info: VideoInfo, pack: dict, progress: ProgressFn) -> list[EventDraft]:
        if info.duration_s < 30:
            return []
        rng = random.Random(int(info.duration_s * 1000))  # same video -> same demo events
        periods = pack["periods"]
        period_len = info.duration_s / periods
        drafts: list[EventDraft] = []

        types = list(pack["_types"].values())
        for i, event_type in enumerate(types):
            for _ in range(COUNTS.get(event_type["id"], DEFAULT_COUNT)):
                t = rng.uniform(5, info.duration_s - 10)
                attrs = {
                    a["id"]: rng.choice(a["values"])
                    for a in event_type["attributes"]
                    if rng.random() < 0.9  # leave some unclassified, like a real model would
                }
                drafts.append(
                    EventDraft(
                        type=event_type["id"],
                        team=rng.choice(["home", "away"]),
                        period=min(periods, int(t // period_len) + 1),
                        t_start=round(t, 2),
                        t_end=round(t + rng.uniform(2, 6), 2),
                        attributes=attrs,
                        confidence=round(rng.uniform(0.45, 0.98), 2),
                    )
                )
            progress((i + 1) / len(types))

        drafts.sort(key=lambda d: d.t_start)
        return drafts
