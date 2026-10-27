from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import update

from . import config, models
from .db import SessionLocal
from .migrate import upgrade_db
from .routers import auth, events, matches, reels, sports


@asynccontextmanager
async def lifespan(app: FastAPI):
    for directory in (config.VIDEO_DIR, config.CLIP_DIR, config.REEL_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    upgrade_db()

    # In-process background jobs die with the server; don't leave matches stuck in 'processing'.
    with SessionLocal() as db:
        db.execute(
            update(models.Match)
            .where(models.Match.status.in_(("uploaded", "processing")))
            .values(status="failed", error="Interrupted by a server restart - use Reprocess")
        )
        db.execute(
            update(models.Reel)
            .where(models.Reel.status.in_(("queued", "processing")))
            .values(status="failed", error="Interrupted by a server restart - export again")
        )
        db.commit()
    yield


app = FastAPI(title="PitchSight", lifespan=lifespan)
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(sports.router, prefix="/api/sports", tags=["sports"])
app.include_router(matches.router, prefix="/api/matches", tags=["matches"])
app.include_router(events.router, prefix="/api/events", tags=["events"])
app.include_router(reels.router, prefix="/api/reels", tags=["reels"])

# Mounted last so /api/* routes win. The UI is plain static files for now.
if config.FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
