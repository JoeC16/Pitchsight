"""Club-level data rights (GDPR): export everything, or delete the club and all its data."""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import config, schemas, storage
from ..auth import AdminUser
from ..db import get_db
from ..models import AuthToken, Club, Event, Match, User, VerifiedSpan

router = APIRouter()


@router.get("/export")
def export_club(admin: AdminUser, db: Session = Depends(get_db)):
    """All of the club's data as JSON (videos are listed, not embedded). No password hashes."""
    club = db.get(Club, admin.club_id)
    matches = db.scalars(select(Match).where(Match.club_id == club.id).order_by(Match.id)).all()
    data = {
        "club": {"id": club.id, "name": club.name, "created_at": club.created_at.isoformat()},
        "users": [{"email": u.email, "role": u.role, "created_at": u.created_at.isoformat()}
                  for u in db.scalars(select(User).where(User.club_id == club.id).order_by(User.email))],
        "matches": [],
    }
    for m in matches:
        events = db.scalars(select(Event).where(Event.match_id == m.id).order_by(Event.t_start)).all()
        spans = db.scalars(select(VerifiedSpan).where(VerifiedSpan.match_id == m.id)).all()
        data["matches"].append({
            "id": m.id, "title": m.title, "sport": m.sport, "home_team": m.home_team, "away_team": m.away_team,
            "played_on": m.played_on.isoformat() if m.played_on else None, "duration_s": m.duration_s,
            "has_video": m.has_video, "pitch_setup": m.pitch_setup,
            "events": [{"type": e.type, "team": e.team, "period": e.period, "t_start": e.t_start, "t_end": e.t_end,
                        "attributes": e.attributes, "confidence": e.confidence, "origin": e.origin,
                        "reviewed": e.reviewed} for e in events],
            "checked_stretches": [[s.t_start, s.t_end] for s in spans],
        })
    name = "".join(c if c.isalnum() else "_" for c in club.name)
    return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="{name}_pitchsight_export.json"'})


@router.delete("", status_code=204)
def delete_club(body: schemas.DeleteClub, admin: AdminUser, db: Session = Depends(get_db)):
    """Delete the club, its users, matches, events and every stored file. Cannot be undone."""
    club = db.get(Club, admin.club_id)
    if body.confirm.strip() != club.name:
        raise HTTPException(422, "Type the club's name exactly to confirm")
    for m in db.scalars(select(Match).where(Match.club_id == club.id)):
        for path in (m.video_path, m.proxy_path, m.video_path and f"{m.video_path}.tracking.json"):
            if path:
                Path(path).unlink(missing_ok=True)
        for directory in (config.CLIP_DIR, config.REEL_DIR):
            for f in directory.glob(f"m{m.id}_*.mp4"):
                f.unlink(missing_ok=True)
        storage.remove(m.video_key, m.proxy_key, *(r.key for r in m.reels))
        db.delete(m)
    for u in db.scalars(select(User).where(User.club_id == club.id)):
        db.delete(u)
    db.execute(delete(AuthToken).where(AuthToken.club_id == club.id))  # outstanding invites
    db.delete(club)
    db.commit()
