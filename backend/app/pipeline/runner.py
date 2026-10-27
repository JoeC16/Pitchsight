"""Runs a detector over a match's video and stores the resulting events.

Currently invoked as a FastAPI background task (in-process). To scale, replace the call
site in routers/matches.py with a queue job (RQ/Celery/Modal) that runs process_match on
a GPU worker -- this function is already self-contained (opens its own DB session).
"""
import logging
from pathlib import Path

from .. import config, taxonomy
from ..db import SessionLocal
from ..models import Event, Match
from . import get_detector
from .probe import probe

log = logging.getLogger(__name__)


def process_match(match_id: int) -> None:
    db = SessionLocal()
    try:
        match = db.get(Match, match_id)
        if match is None or not match.video_path:
            return
        match.status, match.progress, match.error = "processing", 0.0, None
        db.commit()

        video = Path(match.video_path)
        info = probe(video)
        match.duration_s, match.fps = info.duration_s, info.fps
        db.commit()

        detector = get_detector(config.DETECTOR)
        pack = taxonomy.get_pack(match.sport)

        def progress(p: float) -> None:
            match.progress = round(p, 3)
            db.commit()

        dropped = 0
        for draft in detector.detect(video, info, pack, progress):
            try:
                taxonomy.validate_event(match.sport, draft.type, draft.team, draft.attributes)
            except taxonomy.TaxonomyError as exc:  # a detector must not be able to poison the DB
                dropped += 1
                log.warning("match %s: dropped invalid draft: %s", match_id, exc)
                continue
            db.add(
                Event(
                    match_id=match.id, type=draft.type, team=draft.team, period=draft.period,
                    t_start=draft.t_start, t_end=draft.t_end, attributes=draft.attributes,
                    confidence=draft.confidence, origin=detector.name, reviewed=False,
                )
            )
        match.status, match.progress, match.detector = "ready", 1.0, detector.name
        db.commit()
        if dropped:
            log.warning("match %s: %d invalid drafts dropped", match_id, dropped)
    except Exception as exc:  # noqa: BLE001 - surface any failure on the match, don't crash the worker
        db.rollback()
        log.exception("processing match %s failed", match_id)
        match = db.get(Match, match_id)
        if match is not None:
            match.status, match.error = "failed", str(exc)[:500]
            db.commit()
    finally:
        db.close()
