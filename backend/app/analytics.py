"""Match-level aggregates. Generic over sport packs: no sport-specific code here."""
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import taxonomy
from .models import Event, Match


def _score(pack: dict, events: list[Event]) -> dict | None:
    cfg = pack.get("scoring")
    if not cfg:
        return None
    total = {"home": 0, "away": 0}
    goals = {"home": 0, "away": 0}
    for e in events:
        if e.type != cfg["event"] or e.team not in total:
            continue
        outcome = (e.attributes or {}).get(cfg["attribute"])
        pts = cfg["values"].get(outcome)
        if pts:
            total[e.team] += pts
            if outcome == "goal":
                goals[e.team] += 1
    out = {}
    for team in total:
        if cfg.get("display") == "gaa":
            display = f"{goals[team]}-{total[team] - goals[team] * 3:02d} ({total[team]})"
        else:
            display = str(total[team])
        out[team] = {"total": total[team], "goals": goals[team], "display": display}
    return out


def match_summary(db: Session, match: Match) -> dict:
    pack = taxonomy.get_pack(match.sport)
    events = db.scalars(select(Event).where(Event.match_id == match.id)).all()

    counts = {t: {"home": 0, "away": 0, "unassigned": 0} for t in pack["_types"]}
    # breakdown[type][attribute][value][team] -> count
    breakdown: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(int))))
    for e in events:
        team = e.team or "unassigned"
        # setdefault: tolerate events whose type was later removed from the sport pack
        counts.setdefault(e.type, {"home": 0, "away": 0, "unassigned": 0})[team] += 1
        for attr, value in (e.attributes or {}).items():
            breakdown[e.type][attr][value][team] += 1

    return {
        "match_id": match.id,
        "total_events": len(events),
        "reviewed_events": sum(1 for e in events if e.reviewed),
        "score": _score(pack, events),
        "counts": counts,
        "breakdown": breakdown,
    }
