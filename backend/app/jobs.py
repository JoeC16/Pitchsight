"""Background jobs: in-process (inline) or a database-backed queue run by workers.

    submit(background, db, "process_match", match.id)

Queue mode: a job is claimed with one atomic UPDATE ... WHERE status='queued' (safe with many
workers on Postgres or SQLite). A worker heartbeats while it runs a job; a job whose heartbeat
is older than the lease belonged to a worker that died, and is retried up to JOB_MAX_ATTEMPTS,
then marked failed (and its match/reel marked failed with a reason). The job functions record
ordinary failures on the match/reel themselves, so a job is "done" once its function returns.
"""
import logging
import os
import socket
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from . import config
from .models import Job, Match, Reel

log = logging.getLogger(__name__)


def _registry() -> dict[str, Callable[[int], None]]:
    from . import proxy, reels
    from .pipeline.runner import process_match
    return {"process_match": process_match, "build_proxy": proxy.build_proxy, "build_reel": reels.build_reel}


KINDS = ("process_match", "build_proxy", "build_reel")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def submit(background, db: Session, kind: str, target_id: int) -> None:
    """Run `kind` for `target_id` in the background, however this deployment runs jobs."""
    if kind not in KINDS:
        raise ValueError(f"unknown job kind {kind!r}")
    if config.JOBS_MODE == "queue":
        db.add(Job(kind=kind, target_id=target_id))
        db.commit()
    elif background is None:  # e.g. a CLI: no request to attach to, so run it now
        _registry()[kind](target_id)
    else:
        background.add_task(_registry()[kind], target_id)


def claim(db: Session, worker: str, kinds: tuple[str, ...] = KINDS) -> Job | None:
    """Take the oldest queued job of these kinds, or None."""
    for _ in range(5):  # another worker may win the race for a candidate; try the next one
        job_id = db.scalar(select(Job.id).where(Job.status == "queued", Job.kind.in_(kinds)).order_by(Job.id).limit(1))
        if job_id is None:
            return None
        won = db.execute(
            update(Job).where(Job.id == job_id, Job.status == "queued")
            .values(status="running", worker=worker, attempts=Job.attempts + 1, heartbeat_at=_now())
        ).rowcount
        db.commit()
        if won:
            return db.get(Job, job_id)
    return None


def _mark_target_failed(db: Session, job: Job, reason: str) -> None:
    if job.kind == "process_match" and (m := db.get(Match, job.target_id)):
        m.status, m.error = "failed", reason[:500]
    elif job.kind == "build_proxy" and (m := db.get(Match, job.target_id)):
        m.proxy_status = "failed"
    elif job.kind == "build_reel" and (r := db.get(Reel, job.target_id)):
        r.status, r.error = "failed", reason[:500]


def recover_stale(db: Session) -> int:
    """Requeue (or give up on) running jobs whose worker stopped heartbeating."""
    cutoff = _now() - timedelta(seconds=config.JOB_LEASE_S)
    stale = db.scalars(select(Job).where(Job.status == "running", Job.heartbeat_at < cutoff)).all()
    for job in stale:
        if job.attempts >= config.JOB_MAX_ATTEMPTS:
            job.status, job.finished_at = "failed", _now()
            job.error = f"worker stopped responding {job.attempts} times"
            _mark_target_failed(db, job, f"Processing stopped {job.attempts} times (worker crashed or ran out of time)")
        else:
            log.warning("job %s (%s %s) lost its worker %s; requeueing", job.id, job.kind, job.target_id, job.worker)
            job.status, job.worker = "queued", None
    db.commit()
    return len(stale)


def run(job_id: int, session_factory) -> None:
    """Run one claimed job, heartbeating from a side thread while it works."""
    with session_factory() as db:
        job = db.get(Job, job_id)
        kind, target = job.kind, job.target_id
    stop = threading.Event()

    def beat():
        while not stop.wait(min(30.0, config.JOB_LEASE_S / 4)):
            with session_factory() as hb:
                hb.execute(update(Job).where(Job.id == job_id).values(heartbeat_at=_now()))
                hb.commit()

    t = threading.Thread(target=beat, daemon=True)
    t.start()
    error = None
    try:
        _registry()[kind](target)
    except Exception as exc:  # noqa: BLE001 - job functions record their own failures; this is a backstop
        log.exception("job %s crashed", job_id)
        error = str(exc)[:500]
    finally:
        stop.set()
        t.join()
    with session_factory() as db:
        job = db.get(Job, job_id)
        job.status, job.finished_at, job.error = ("failed" if error else "done"), _now(), error
        if error:
            _mark_target_failed(db, job, error)
        db.commit()


def worker_name() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"
