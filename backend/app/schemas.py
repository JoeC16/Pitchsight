from datetime import date, datetime
from typing import Annotated, Literal

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
    width: int | None
    height: int | None
    video_codec: str | None
    proxy_status: str
    proxy_reason: str | None
    proxy_progress: float
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


# ---------- auth ----------
Email = Annotated[str, Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")]
Password = Annotated[str, Field(min_length=8, max_length=200)]


class Register(BaseModel):
    club_name: str = Field(min_length=1, max_length=120)
    email: Email
    password: Password


class Login(BaseModel):
    email: str
    password: str


class NewUser(BaseModel):
    email: Email
    password: Password
    role: Literal["admin", "member"] = "member"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    role: str


class ClubOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class Me(UserOut):
    club: ClubOut


class ReelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    match_id: int
    filters: str
    event_count: int
    status: str
    progress: float
    error: str | None
    segment_count: int | None
    duration_s: float | None
    created_at: datetime
