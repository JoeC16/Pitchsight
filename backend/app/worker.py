"""Job worker: python -m app.worker [--kinds process_match,build_proxy,build_reel] [--once]

Run one or more of these alongside the API when PITCHSIGHT_JOBS=queue. Split by kind to put
detection on GPU machines and ffmpeg work (proxies, reels) on cheaper ones, e.g.
    python -m app.worker --kinds process_match        # GPU box
    python -m app.worker --kinds build_proxy,build_reel
"""
import argparse
import logging
import signal
import time

from . import jobs
from .db import SessionLocal
from .migrate import upgrade_db

log = logging.getLogger("pitchsight.worker")


def work(kinds: tuple[str, ...] = jobs.KINDS, once: bool = False, poll_s: float = 2.0, stop=lambda: False) -> int:
    """Process jobs until stopped (or until the queue is empty with once=True). Returns jobs run."""
    name, done = jobs.worker_name(), 0
    while not stop():
        with SessionLocal() as db:
            jobs.recover_stale(db)
            job = jobs.claim(db, name, kinds)
            job_id = job.id if job else None
        if job_id is None:
            if once:
                return done
            time.sleep(poll_s)
            continue
        log.info("running job %s", job_id)
        jobs.run(job_id, SessionLocal)
        done += 1
    return done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.worker")
    ap.add_argument("--kinds", default=",".join(jobs.KINDS))
    ap.add_argument("--once", action="store_true", help="exit when the queue is empty")
    args = ap.parse_args(argv)
    kinds = tuple(k for k in args.kinds.split(",") if k)
    bad = set(kinds) - set(jobs.KINDS)
    if bad:
        ap.error(f"unknown kinds {sorted(bad)}; choose from {jobs.KINDS}")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    upgrade_db()
    stopping = []
    for sig in (signal.SIGTERM, signal.SIGINT):  # finish the current job, then exit
        signal.signal(sig, lambda *_: stopping.append(True))
    n = work(kinds, args.once, stop=lambda: bool(stopping))
    log.info("worker stopped after %d job(s)", n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
