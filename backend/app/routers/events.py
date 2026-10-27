from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from .. import clips, schemas, taxonomy
from ..auth import CurrentUser
from ..db import get_db
from ..models import Event, User

router = APIRouter()


def _get_event(db: Session, event_id: int, user: User) -> Event:
    """The event, if its match belongs to the user's club. Otherwise a plain 404."""
    event = db.get(Event, event_id)
    if event is None or event.match.club_id != user.club_id:
        raise HTTPException(404, "Event not found")
    return event


@router.get("/{event_id}", response_model=schemas.EventOut)
def get_event(event_id: int, user: CurrentUser, db: Session = Depends(get_db)):
    return _get_event(db, event_id, user)


@router.patch("/{event_id}", response_model=schemas.EventOut)
def patch_event(event_id: int, body: schemas.EventPatch, user: CurrentUser, db: Session = Depends(get_db)):
    event = _get_event(db, event_id, user)
    match = event.match
    data = body.model_dump(exclude_unset=True)

    new_type = data.get("type", event.type)
    new_team = data["team"] if "team" in data else event.team
    new_attrs = data["attributes"] if data.get("attributes") is not None else event.attributes
    t_start = data.get("t_start", event.t_start)
    t_end = data.get("t_end", event.t_end)
    if t_end < t_start:
        raise HTTPException(422, "t_end must be >= t_start")
    try:
        taxonomy.validate_event(match.sport, new_type, new_team, new_attrs)
    except taxonomy.TaxonomyError as exc:
        raise HTTPException(422, str(exc)) from None

    event.type, event.team, event.attributes = new_type, new_team, dict(new_attrs)
    event.t_start, event.t_end = t_start, t_end
    event.period = data.get("period", event.period)
    # Any human edit counts as a review, unless the caller says otherwise.
    event.reviewed = data.get("reviewed", True)
    db.commit()
    return event


@router.delete("/{event_id}", status_code=204)
def delete_event(event_id: int, user: CurrentUser, db: Session = Depends(get_db)):
    db.delete(_get_event(db, event_id, user))
    db.commit()


@router.get("/{event_id}/clip")
def event_clip(event_id: int, user: CurrentUser, db: Session = Depends(get_db)):
    """Cut (or fetch from cache) an mp4 clip around the event. Sync route -> runs in a threadpool."""
    event = _get_event(db, event_id, user)
    try:
        path = clips.get_or_cut_clip(event.match, event)
    except clips.ClipError as exc:
        raise HTTPException(500, str(exc)) from None
    # The demo banner doesn't travel with a downloaded file, so label simulated events in its name.
    demo = "_DEMO-simulated-event" if event.origin == "stub" else ""
    return FileResponse(path, media_type="video/mp4", filename=f"{event.type}_{int(event.t_start)}s{demo}.mp4")
