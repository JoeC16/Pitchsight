"""Sport packs: YAML taxonomies that define what events and classifications exist."""
from functools import lru_cache

import yaml

from . import config

TEAMS = ("home", "away")


class TaxonomyError(ValueError):
    """An event does not fit the sport pack."""


@lru_cache
def load_packs() -> dict[str, dict]:
    packs: dict[str, dict] = {}
    for path in sorted(config.SPORT_PACK_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["_types"] = {et["id"]: et for et in data["event_types"]}
        packs[data["sport"]] = data
    return packs


def get_pack(sport: str) -> dict:
    try:
        return load_packs()[sport]
    except KeyError:
        raise TaxonomyError(f"Unknown sport '{sport}'") from None


def public_pack(sport: str) -> dict:
    """The pack as sent to the UI (internal keys stripped)."""
    return {k: v for k, v in get_pack(sport).items() if not k.startswith("_")}


def validate_event(sport: str, type_: str, team: str | None, attributes: dict[str, str]) -> None:
    pack = get_pack(sport)
    event_type = pack["_types"].get(type_)
    if event_type is None:
        raise TaxonomyError(f"'{type_}' is not an event type in {sport}")
    if team is not None and team not in TEAMS:
        raise TaxonomyError("team must be 'home', 'away' or empty")
    allowed = {a["id"]: a["values"] for a in event_type["attributes"]}
    for key, value in (attributes or {}).items():
        if key not in allowed:
            raise TaxonomyError(f"'{key}' is not an attribute of {type_}")
        if value not in allowed[key]:
            raise TaxonomyError(f"'{value}' is not a valid {key} for {type_}")
