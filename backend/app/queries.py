"""Event filtering. Every UI filter is one of these query parameters:

    type=shot,turnover        event types (comma = OR)
    team=home|away
    period=1|2
    min_conf=0.6              hides AI events below this confidence (manual tags always pass)
    attr.<id>=a,b             attribute value filter, e.g. attr.zone=middle_third,inside_45
"""
import re
from collections.abc import Mapping

from sqlalchemy import Select, or_, select

from .models import Event

_ATTR_KEY = re.compile(r"^[a-z0-9_]+$")


def _split(value: str) -> list[str]:
    return [v for v in value.split(",") if v]


def filter_events(match_id: int, params: Mapping[str, str]) -> Select:
    stmt = select(Event).where(Event.match_id == match_id)

    if v := params.get("type"):
        stmt = stmt.where(Event.type.in_(_split(v)))
    if v := params.get("team"):
        stmt = stmt.where(Event.team == v)
    if v := params.get("period"):
        stmt = stmt.where(Event.period == int(v))
    if v := params.get("min_conf"):
        stmt = stmt.where(or_(Event.confidence.is_(None), Event.confidence >= float(v)))

    for key, value in params.items():
        if not key.startswith("attr."):
            continue
        name = key[len("attr."):]
        if not _ATTR_KEY.match(name):
            raise ValueError(f"bad attribute filter '{key}'")
        stmt = stmt.where(Event.attributes[name].as_string().in_(_split(value)))

    return stmt.order_by(Event.t_start)
