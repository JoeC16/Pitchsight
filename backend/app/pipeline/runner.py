"""Runs a detector over a match's video and stores the resulting events.

Currently invoked as a FastAPI background task (in-process). To scale, replace the call
site in routers/matches.py with a queue job (RQ/Celery/Modal) that runs process_match on
a GPU worker -- this function is already self-contained (opens its own DB session).
"""
import logging
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import select

from .. import config, storage, taxonomy
from ..proxy import build_proxy, needs_proxy
from ..db import SessionLocal
from ..models import DetectionRun, Event, Job, Match
from . import get_detector
from .interfaces import MatchContext
from .probe import probe

log = logging.getLogger(__name__)

# A new detection this close to a kept (manual/reviewed) event of the same type is the same event.
DUPLICATE_WINDOW_S = 2.0


def process_match(match_id: int) -> None:
    db = SessionLocal()
    try:
        match = db.get(Match, match_id)
        if match is None or not match.has_video:
            return
        match.status, match.progress, match.error = "processing", 0.0, None
        db.commit()

        if storage.remote() is not None and not match.video_key and match.video_path:
            match.video_key = storage.persist(Path(match.video_path), f"videos/m{match.id}{Path(match.video_path).suffix}")
            db.commit()
        video = storage.ensure_local(match.video_path, match.video_key)
        info = probe(video)
        match.duration_s, match.fps = info.duration_s, info.fps
        match.width, match.height, match.video_codec = info.width, info.height, info.video_codec
        if match.proxy_status != "ready":
            match.proxy_reason = needs_proxy(info, video)
            match.proxy_status = "pending" if match.proxy_reason else "none"
        db.commit()

        detector = get_detector(config.DETECTOR)
        pack = taxonomy.get_pack(match.sport)

        def progress(p: float) -> None:
            match.progress = round(p, 3)
            db.commit()

        # Events a human made or checked survive reprocessing; don't add a second copy of them.
        kept = db.scalars(select(Event).where(Event.match_id == match.id)).all()

        dropped, valid = 0, []
        second_half = (match.pitch_setup or {}).get("second_half_s")
        context = MatchContext(sport=match.sport, pitch_setup=match.pitch_setup,
                               period_starts=[0.0, second_half] if second_half else None)
        for draft in detector.detect(video, info, pack, progress, context):
            try:
                taxonomy.validate_event(match.sport, draft.type, draft.team, draft.attributes)
            except taxonomy.TaxonomyError as exc:  # a detector must not be able to poison the DB
                dropped += 1
                log.warning("match %s: dropped invalid draft: %s", match_id, exc)
                continue
            valid.append(draft)
            if any(k.type == draft.type and abs(k.t_start - draft.t_start) <= DUPLICATE_WINDOW_S for k in kept):
                continue
            db.add(
                Event(
                    match_id=match.id, type=draft.type, team=draft.team, period=draft.period,
                    t_start=draft.t_start, t_end=draft.t_end, attributes=draft.attributes,
                    confidence=draft.confidence, origin=detector.name, reviewed=False,
                )
            )
        db.add(DetectionRun(match_id=match.id, detector=detector.name,
                            drafts=[asdict(d) for d in valid], dropped=dropped))
        match.status, match.progress, match.detector = "ready", 1.0, detector.name
        db.commit()
        if dropped:
            log.warning("match %s: %d invalid drafts dropped", match_id, dropped)
        # After detection so events show up first; until it's ready the original is served.
        if match.proxy_status == "pending":
            if config.JOBS_MODE == "queue":
                db.add(Job(kind="build_proxy", target_id=match_id))  # may run on a cheaper worker
                db.commit()
            else:
                db.close()
                build_proxy(match_id)
    except Exception as exc:  # noqa: BLE001 - surface any failure on the match, don't crash the worker
        db.rollback()
        log.exception("processing match %s failed", match_id)
        match = db.get(Match, match_id)
        if match is not None:
            match.status, match.error = "failed", str(exc)[:500]
            if match.proxy_status == "pending":
                match.proxy_status = "none"  # never started
            db.commit()
    finally:
        db.close()
