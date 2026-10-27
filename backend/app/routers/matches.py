import mimetypes
from pathlib import Path
from urllib.parse import unquote

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import analytics, config, queries, reels, schemas, taxonomy
from ..auth import CurrentUser
from ..db import get_db
from ..models import Event, Match, Reel, User
from ..pipeline.runner import process_match

router = APIRouter()


def _get_match(db: Session, match_id: int, user: User) -> Match:
    """The match, if it belongs to the user's club. Other clubs' matches are a plain 404."""
    match = db.get(Match, match_id)
    if match is None or match.club_id != user.club_id:
        raise HTTPException(404, "Match not found")
    return match


@router.post("", response_model=schemas.MatchOut, status_code=201)
def create_match(body: schemas.MatchCreate, user: CurrentUser, db: Session = Depends(get_db)):
    if body.sport not in taxonomy.load_packs():
        raise HTTPException(422, f"Unknown sport '{body.sport}'")
    match = Match(**body.model_dump(), club_id=user.club_id)
    db.add(match)
    db.commit()
    return match


@router.get("", response_model=list[schemas.MatchOut])
def list_matches(user: CurrentUser, db: Session = Depends(get_db)):
    return db.scalars(
        select(Match).where(Match.club_id == user.club_id).order_by(Match.created_at.desc())
    ).all()


@router.get("/{match_id}", response_model=schemas.MatchOut)
def get_match(match_id: int, user: CurrentUser, db: Session = Depends(get_db)):
    return _get_match(db, match_id, user)


@router.delete("/{match_id}", status_code=204)
def delete_match(match_id: int, user: CurrentUser, db: Session = Depends(get_db)):
    match = _get_match(db, match_id, user)
    if match.video_path:
        Path(match.video_path).unlink(missing_ok=True)
    for directory in (config.CLIP_DIR, config.REEL_DIR):
        for f in directory.glob(f"m{match.id}_*.mp4"):
            f.unlink(missing_ok=True)
    db.delete(match)
    db.commit()


@router.put("/{match_id}/video", response_model=schemas.MatchOut)
async def upload_video(
    match_id: int, request: Request, background: BackgroundTasks, user: CurrentUser,
    db: Session = Depends(get_db),
):
    """Upload the match video as a raw request body (not multipart), streamed straight to
    disk so multi-GB Veo files never sit in memory. Send the original name in X-Filename."""
    match = _get_match(db, match_id, user)
    if match.status not in ("created", "failed"):
        raise HTTPException(409, f"Match is '{match.status}'; video can only be uploaded once")

    declared = int(request.headers.get("content-length") or 0)
    if declared > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large")
    ext = Path(unquote(request.headers.get("x-filename", ""))).suffix.lower() or ".mp4"
    if ext not in config.ALLOWED_VIDEO_EXTS:
        raise HTTPException(415, f"Unsupported file type '{ext}'")

    config.VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    dest = config.VIDEO_DIR / f"{match.id}{ext}"
    part = dest.with_name(dest.name + ".part")
    written = 0
    try:
        with open(part, "wb") as out:
            async for chunk in request.stream():
                written += len(chunk)
                if written > config.MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "File too large")
                await run_in_threadpool(out.write, chunk)
        if written == 0:
            raise HTTPException(400, "Empty upload")
        part.replace(dest)
    except BaseException:
        part.unlink(missing_ok=True)
        raise

    match.video_path, match.status, match.error = str(dest), "uploaded", None
    db.commit()
    background.add_task(process_match, match.id)
    return match


@router.get("/{match_id}/video")
def get_video(match_id: int, user: CurrentUser, db: Session = Depends(get_db)):
    match = _get_match(db, match_id, user)
    if not match.video_path or not Path(match.video_path).exists():
        raise HTTPException(404, "No video")
    media_type = mimetypes.guess_type(match.video_path)[0] or "video/mp4"
    return FileResponse(match.video_path, media_type=media_type)  # supports Range requests


@router.post("/{match_id}/reprocess", response_model=schemas.MatchOut)
def reprocess(match_id: int, background: BackgroundTasks, user: CurrentUser, db: Session = Depends(get_db)):
    """Re-run detection. Keeps manual and human-reviewed events; replaces the rest."""
    match = _get_match(db, match_id, user)
    if not match.video_path:
        raise HTTPException(409, "Upload a video first")
    if match.status == "processing":
        raise HTTPException(409, "Already processing")
    db.execute(delete(Event).where(Event.match_id == match.id, Event.origin != "manual", Event.reviewed.is_(False)))
    match.status, match.progress, match.error = "uploaded", 0.0, None
    db.commit()
    background.add_task(process_match, match.id)
    return match


@router.get("/{match_id}/events", response_model=list[schemas.EventOut])
def list_events(match_id: int, request: Request, user: CurrentUser, db: Session = Depends(get_db)):
    _get_match(db, match_id, user)
    try:
        stmt = queries.filter_events(match_id, request.query_params)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return db.scalars(stmt).all()


@router.post("/{match_id}/events", response_model=schemas.EventOut, status_code=201)
def create_event(match_id: int, body: schemas.EventIn, user: CurrentUser, db: Session = Depends(get_db)):
    """Manual tag. Stored as origin='manual', reviewed."""
    match = _get_match(db, match_id, user)
    try:
        taxonomy.validate_event(match.sport, body.type, body.team, body.attributes)
    except taxonomy.TaxonomyError as exc:
        raise HTTPException(422, str(exc)) from None
    event = Event(match_id=match.id, **body.model_dump(), confidence=None, origin="manual", reviewed=True)
    db.add(event)
    db.commit()
    return event


@router.get("/{match_id}/summary")
def summary(match_id: int, user: CurrentUser, db: Session = Depends(get_db)):
    return analytics.match_summary(db, _get_match(db, match_id, user))


@router.post("/{match_id}/reels", response_model=schemas.ReelOut, status_code=202)
def create_reel(
    match_id: int, request: Request, background: BackgroundTasks, user: CurrentUser, db: Session = Depends(get_db)
):
    """Export the events matching the query-string filters (same as GET /events) as one mp4.
    Returns immediately; poll GET /api/reels/{id} until status is 'ready', then download."""
    match = _get_match(db, match_id, user)
    if not match.video_path:
        raise HTTPException(409, "Upload a video first")
    try:
        stmt = queries.filter_events(match_id, request.query_params)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    ids = [e.id for e in db.scalars(stmt)]
    if not ids:
        raise HTTPException(422, "No events match these filters")
    if len(ids) > config.MAX_REEL_EVENTS:
        raise HTTPException(422, f"{len(ids)} events match; narrow the filters to at most {config.MAX_REEL_EVENTS}")
    reel = Reel(match_id=match.id, filters=str(request.query_params), event_ids=ids)
    db.add(reel)
    db.commit()
    background.add_task(reels.build_reel, reel.id)
    return reel


@router.get("/{match_id}/reels", response_model=list[schemas.ReelOut])
def list_reels(match_id: int, user: CurrentUser, db: Session = Depends(get_db)):
    _get_match(db, match_id, user)
    return db.scalars(select(Reel).where(Reel.match_id == match_id).order_by(Reel.created_at.desc())).all()
