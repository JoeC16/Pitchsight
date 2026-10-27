"""Reel export: join the clips for a set of events into one mp4. Runs as a background job."""
import logging
import subprocess
import tempfile
from pathlib import Path

from sqlalchemy import select

from . import clips, config
from .db import SessionLocal
from .models import Event, Match, Reel

log = logging.getLogger(__name__)


def merge_windows(windows: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Merge overlapping or touching windows so no footage plays twice."""
    merged: list[list[float]] = []
    for start, end in sorted(windows):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def reel_path(reel: Reel) -> Path:
    return config.REEL_DIR / f"m{reel.match_id}_r{reel.id}.mp4"


def concat(parts: list[Path], dest: Path) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as listing:
        for p in parts:
            listing.write(f"file '{p.resolve()}'\n")
    tmp = dest.with_suffix(".part.mp4")
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", listing.name,
             "-c", "copy", "-movflags", "+faststart", str(tmp)],
            capture_output=True, text=True, timeout=1800,
        )
        if result.returncode != 0 or not tmp.exists():
            raise clips.ClipError(f"ffmpeg concat failed: {result.stderr.strip()[:300]}")
        tmp.replace(dest)
    finally:
        Path(listing.name).unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)


def build_reel(reel_id: int) -> None:
    db = SessionLocal()
    try:
        reel = db.get(Reel, reel_id)
        if reel is None:
            return
        match: Match = reel.match
        reel.status, reel.progress, reel.error = "processing", 0.0, None
        db.commit()

        # Events deleted since the request are skipped.
        events = db.scalars(select(Event).where(Event.id.in_(reel.event_ids), Event.match_id == match.id)).all()
        if not events:
            raise clips.ClipError("None of the reel's events exist any more")
        windows = merge_windows([clips.clip_window(e, match.duration_s) for e in events])
        parts = []
        for i, (start, end) in enumerate(windows):
            parts.append(clips.get_or_cut_window(match, start, end))
            reel.progress = round(0.95 * (i + 1) / len(windows), 3)
            db.commit()

        config.REEL_DIR.mkdir(parents=True, exist_ok=True)
        dest = reel_path(reel)
        concat(parts, dest)
        reel.path, reel.segment_count = str(dest), len(windows)
        reel.duration_s = round(sum(e - s for s, e in windows), 2)
        reel.status, reel.progress = "ready", 1.0
        db.commit()
    except Exception as exc:  # noqa: BLE001 - surface any failure on the reel
        db.rollback()
        log.exception("building reel %s failed", reel_id)
        reel = db.get(Reel, reel_id)
        if reel is not None:
            reel.status, reel.error = "failed", str(exc)[:500]
            db.commit()
    finally:
        db.close()
