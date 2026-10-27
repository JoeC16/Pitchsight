from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from .. import schemas
from ..auth import CurrentUser
from ..db import get_db
from ..models import Reel, User

router = APIRouter()


def _get_reel(db: Session, reel_id: int, user: User) -> Reel:
    reel = db.get(Reel, reel_id)
    if reel is None or reel.match.club_id != user.club_id:
        raise HTTPException(404, "Reel not found")
    return reel


@router.get("/{reel_id}", response_model=schemas.ReelOut)
def get_reel(reel_id: int, user: CurrentUser, db: Session = Depends(get_db)):
    return _get_reel(db, reel_id, user)


@router.get("/{reel_id}/download")
def download_reel(reel_id: int, user: CurrentUser, db: Session = Depends(get_db)):
    reel = _get_reel(db, reel_id, user)
    if reel.status != "ready" or not reel.path or not Path(reel.path).exists():
        raise HTTPException(409, f"Reel is '{reel.status}', not ready to download")
    # The demo banner doesn't travel with a downloaded file, so label simulated content in its name.
    demo = "_DEMO-simulated-events" if reel.match.detector == "stub" else ""
    name = f"{reel.match.title}_reel_{reel.id}{demo}.mp4".replace("/", "-")
    return FileResponse(reel.path, media_type="video/mp4", filename=name)


@router.delete("/{reel_id}", status_code=204)
def delete_reel(reel_id: int, user: CurrentUser, db: Session = Depends(get_db)):
    reel = _get_reel(db, reel_id, user)
    if reel.status in ("queued", "processing"):
        raise HTTPException(409, "Reel is still being built")
    if reel.path:
        Path(reel.path).unlink(missing_ok=True)
    db.delete(reel)
    db.commit()
