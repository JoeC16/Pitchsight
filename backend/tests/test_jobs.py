import shutil
import subprocess
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import config, jobs
from app.db import SessionLocal
from app.models import Job, Match, Reel
from app.worker import work
from tests.test_api import fake_probe, upload  # noqa: F401

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


@pytest.fixture()
def queue_mode(monkeypatch):
    monkeypatch.setattr(config, "JOBS_MODE", "queue")
    with SessionLocal() as db:  # start each test with an empty queue
        for j in db.scalars(select(Job)):
            db.delete(j)
        db.commit()


def match_status(mid):
    with SessionLocal() as db:
        m = db.get(Match, mid)
        return m.status, m.error


def test_upload_is_queued_then_processed_by_a_worker(client, match, queue_mode, fake_probe):  # noqa: F811
    m = match["id"]
    upload(client, m)
    assert match_status(m)[0] == "uploaded"  # nothing ran in the API process
    with SessionLocal() as db:
        [job] = db.scalars(select(Job)).all()
        assert (job.kind, job.target_id, job.status) == ("process_match", m, "queued")
    assert work(once=True) == 1
    assert match_status(m)[0] == "ready"
    with SessionLocal() as db:
        job = db.scalars(select(Job)).one()
        assert job.status == "done" and job.attempts == 1 and job.finished_at


def test_claim_is_exclusive(queue_mode, match):
    with SessionLocal() as db:
        db.add(Job(kind="process_match", target_id=match["id"]))
        db.commit()
    with SessionLocal() as a, SessionLocal() as b:
        first, second = jobs.claim(a, "w1"), jobs.claim(b, "w2")
    assert first is not None and first.worker == "w1" and second is None


def test_kinds_filter(queue_mode, match):
    with SessionLocal() as db:
        db.add(Job(kind="build_reel", target_id=999999))
        db.commit()
        assert jobs.claim(db, "gpu", ("process_match",)) is None
        assert jobs.claim(db, "cpu", ("build_reel",)).kind == "build_reel"


def test_dead_worker_job_is_retried_then_given_up(queue_mode, match, monkeypatch):
    monkeypatch.setattr(config, "JOB_MAX_ATTEMPTS", 2)
    m = match["id"]
    with SessionLocal() as db:
        db.add(Job(kind="process_match", target_id=m))
        db.commit()
    old = datetime.now(timezone.utc) - timedelta(seconds=config.JOB_LEASE_S + 60)
    for attempt in (1, 2):
        with SessionLocal() as db:
            job = jobs.claim(db, f"doomed-{attempt}")
            assert job and job.attempts == attempt
            job.heartbeat_at = old  # ...and the worker died without finishing
            db.commit()
            jobs.recover_stale(db)
            job = db.get(Job, job.id)
            db.refresh(job)
    assert job.status == "failed" and "2 times" in job.error
    status, error = match_status(m)
    assert status == "failed" and "stopped 2 times" in error


def test_first_lost_lease_requeues(queue_mode, match):
    with SessionLocal() as db:
        db.add(Job(kind="process_match", target_id=match["id"]))
        db.commit()
        job = jobs.claim(db, "w")
        job.heartbeat_at = datetime.now(timezone.utc) - timedelta(seconds=config.JOB_LEASE_S + 1)
        db.commit()
        assert jobs.recover_stale(db) == 1
        db.refresh(job)
        assert job.status == "queued" and job.worker is None and job.attempts == 1


def test_crashing_job_function_is_recorded(queue_mode, match, monkeypatch):
    m = match["id"]
    monkeypatch.setattr(jobs, "_registry", lambda: {"process_match": lambda _id: 1 / 0})
    with SessionLocal() as db:
        db.add(Job(kind="process_match", target_id=m))
        db.commit()
    work(once=True)
    with SessionLocal() as db:
        job = db.scalars(select(Job)).one()
        assert job.status == "failed" and "division by zero" in job.error
    assert match_status(m) == ("failed", "division by zero")


def test_api_restart_leaves_queued_work_alone(queue_mode, match):
    from fastapi.testclient import TestClient

    from app.main import app
    with SessionLocal() as db:
        db.get(Match, match["id"]).status = "processing"
        db.commit()
    with TestClient(app):
        pass  # lifespan ran
    assert match_status(match["id"])[0] == "processing"


@needs_ffmpeg
def test_proxy_and_reel_jobs_through_the_queue(client, match, queue_mode, tmp_path):
    video = tmp_path / "g.mkv"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=60:size=160x120:rate=10",
                    "-c:v", "mpeg4", str(video)], check=True)
    m = match["id"]
    upload(client, m, data=video.read_bytes(), name="g.mkv")
    assert work(once=True, kinds=("process_match",)) == 1           # detection only...
    with SessionLocal() as db:
        assert db.get(Match, m).proxy_status == "pending"           # ...proxy queued for another worker
        assert db.scalars(select(Job.kind).where(Job.status == "queued")).all() == ["build_proxy"]
    assert work(once=True, kinds=("build_proxy", "build_reel")) == 1
    with SessionLocal() as db:
        assert db.get(Match, m).proxy_status == "ready"
    reel = client.post(f"/api/matches/{m}/reels", params={"type": "kickout"}).json()
    assert client.get(f"/api/reels/{reel['id']}").json()["status"] == "queued"
    work(once=True)
    with SessionLocal() as db:
        assert db.get(Reel, reel["id"]).status == "ready"


def test_worker_cli_rejects_unknown_kinds():
    from app.worker import main
    with pytest.raises(SystemExit):
        main(["--kinds", "mine_bitcoin", "--once"])


def test_heartbeat_keeps_a_long_job_alive(queue_mode, match, monkeypatch):
    import threading
    import time
    monkeypatch.setattr(config, "JOB_LEASE_S", 0.4)  # heartbeat every 0.1s
    stolen = []

    def slow_job(_id):  # runs for 3 leases; another worker keeps checking for dead jobs meanwhile
        for _ in range(6):
            time.sleep(0.2)
            with SessionLocal() as db:
                stolen.append(jobs.recover_stale(db))

    monkeypatch.setattr(jobs, "_registry", lambda: {"process_match": slow_job})
    with SessionLocal() as db:
        db.add(Job(kind="process_match", target_id=match["id"]))
        db.commit()
    assert work(once=True) == 1
    assert sum(stolen) == 0  # never considered dead
    with SessionLocal() as db:
        assert db.scalars(select(Job)).one().status == "done"
    assert threading.active_count() < 10  # heartbeat thread stopped
