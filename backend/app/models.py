from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    sport: Mapped[str] = mapped_column(String(40))
    home_team: Mapped[str] = mapped_column(String(120))
    away_team: Mapped[str] = mapped_column(String(120))
    played_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    video_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # created -> uploaded -> processing -> ready | failed
    status: Mapped[str] = mapped_column(String(20), default="created")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    fps: Mapped[float | None] = mapped_column(Float, nullable=True)
    detector: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    events: Mapped[list["Event"]] = relationship(
        back_populates="match", cascade="all, delete-orphan"
    )

    @property
    def has_video(self) -> bool:
        return bool(self.video_path)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(40), index=True)
    team: Mapped[str | None] = mapped_column(String(10), nullable=True)  # home | away | None
    period: Mapped[int] = mapped_column(Integer, default=1)
    t_start: Mapped[float] = mapped_column(Float, index=True)  # seconds into the video
    t_end: Mapped[float] = mapped_column(Float)
    # Free-form classifications, validated against the match's sport pack.
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)  # None for manual tags
    origin: Mapped[str] = mapped_column(String(20), default="manual")  # stub | ai | manual
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)

    match: Mapped[Match] = relationship(back_populates="events")
