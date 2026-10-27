from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Team = Literal["home", "away"]


class MatchCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    sport: str
    home_team: str = Field(min_length=1, max_length=120)
    away_team: str = Field(min_length=1, max_length=120)
    played_on: date | None = None


class MatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    sport: str
    home_team: str
    away_team: str
    played_on: date | None
    status: str
    progress: float
    error: str | None
    duration_s: float | None
    fps: float | None
    detector: str | None
    has_video: bool
    created_at: datetime


class EventIn(BaseModel):
    type: str
    team: Team | None = None
    period: int = Field(default=1, ge=1, le=4)
    t_start: float = Field(ge=0)
    t_end: float = Field(ge=0)
    attributes: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _ordered(self):
        if self.t_end < self.t_start:
            raise ValueError("t_end must be >= t_start")
        return self


class EventPatch(BaseModel):
    """Partial update. `attributes`, when sent, replaces the whole dict."""

    type: str | None = None
    team: Team | None = None
    period: int | None = Field(default=None, ge=1, le=4)
    t_start: float | None = Field(default=None, ge=0)
    t_end: float | None = Field(default=None, ge=0)
    attributes: dict[str, str] | None = None
    reviewed: bool | None = None


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    match_id: int
    type: str
    team: Team | None
    period: int
    t_start: float
    t_end: float
    attributes: dict[str, str]
    confidence: float | None
    origin: str
    reviewed: bool
