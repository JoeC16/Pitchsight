from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import update

from . import config, models
from .db import SessionLocal
from .migrate import upgrade_db
from .routers import events, matches, sports


@asynccontextmanager
async def lifespan(app: FastAPI):
    for directory in (config.VIDEO_DIR, config.CLIP_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    upgrade_db()

    # In-process background jobs die with the server; don't leave matches stuck in 'processing'.
    with SessionLocal() as db:
        db.execute(
            update(models.Match)
            .where(models.Match.status == "processing")
            .values(status="failed", error="Interrupted by a server restart - use Reprocess")
        )
        db.commit()
    yield


app = FastAPI(title="PitchSight", lifespan=lifespan)
app.include_router(sports.router, prefix="/api/sports", tags=["sports"])
app.include_router(matches.router, prefix="/api/matches", tags=["matches"])
app.include_router(events.router, prefix="/api/events", tags=["events"])

# Mounted last so /api/* routes win. The UI is plain static files for now.
if config.FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
