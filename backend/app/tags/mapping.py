"""Map raw tags to sport-pack events with a per-provider YAML mapping.

    sport: gaa_football
    time_offset_s: 0                 # added to every tag time (per-game offsets: see the dataset manifest)
    teams:                           # names that identify each team in codes/labels (overridable per game)
      home: [Home, "St Mary"]
      away: [Away, Ballyboden]
    ignore: ["Half start", "Sub*"]   # codes to skip without reporting them as unmapped
    events:                          # first matching rule wins
      - match: {code: "Kickout*"}            # glob on the code (case-insensitive); or code_regex
        type: kickout
        attributes:
          outcome: {from_label: Outcome, map: {"Won clean": retained_clean, "Lost clean": lost_clean}}
          length:  {from_code: "(short|medium|long)", map: {short: short, medium: medium, long: long}}
      - match: {code: "Turnover lost*"}
        type: turnover
        team_means: lost             # the tag names the team that LOST it; the pack wants the winner
      - match: {code: "Shot*", label: {Result: "Wide"}}
        type: shot
        team: home                   # fixed team (e.g. tags only for one side), instead of name lookup
        attributes: {outcome: {value: wide}}
"""
import fnmatch
import re
from collections import Counter
from dataclasses import dataclass, field

import yaml

from .. import taxonomy
from . import RawTag


@dataclass
class Imported:
    type: str
    team: str | None
    t_start: float
    t_end: float
    attributes: dict[str, str]
    code: str
    row: int


@dataclass
class Report:
    total: int = 0
    mapped: int = 0
    ignored: int = 0
    unmapped_codes: Counter = field(default_factory=Counter)
    unmapped_values: dict[str, Counter] = field(default_factory=dict)   # "type.attr" -> raw text counts
    no_team: Counter = field(default_factory=Counter)                   # type -> count
    invalid: list[tuple[int, str, str]] = field(default_factory=list)    # (row, code, reason)

    def ok(self) -> bool:
        return not (self.unmapped_codes or self.unmapped_values or self.invalid)

    def text(self) -> str:
        lines = [f"{self.total} tags: {self.mapped} mapped, {self.ignored} ignored, "
                 f"{sum(self.unmapped_codes.values())} unmapped, {len(self.invalid)} invalid"]
        if self.unmapped_codes:
            lines.append("Unmapped codes (add a rule or list them under ignore):")
            lines += [f"  {n:>5}  {c}" for c, n in self.unmapped_codes.most_common()]
        for key, vals in sorted(self.unmapped_values.items()):
            lines.append(f"Unmapped values for {key} (add them to its map):")
            lines += [f"  {n:>5}  {v}" for v, n in vals.most_common()]
        if self.no_team:
            lines.append("Events with no team found (check teams: names): " +
                         ", ".join(f"{t} {n}" for t, n in self.no_team.most_common()))
        for row, code, reason in self.invalid[:20]:
            lines.append(f"Invalid (row {row}, {code!r}): {reason}")
        if len(self.invalid) > 20:
            lines.append(f"... and {len(self.invalid) - 20} more invalid")
        return "\n".join(lines)


class MappingError(ValueError):
    pass


def load_mapping(path) -> dict:
    m = yaml.safe_load(open(path, encoding="utf-8")) or {}
    if "sport" not in m or "events" not in m:
        raise MappingError(f"{path}: a mapping needs 'sport' and 'events'")
    pack = taxonomy.get_pack(m["sport"])
    for i, rule in enumerate(m["events"]):
        if rule.get("type") not in pack["_types"]:
            raise MappingError(f"{path}: events[{i}] type {rule.get('type')!r} isn't in the {m['sport']} pack")
        allowed = {a["id"]: a["values"] for a in pack["_types"][rule["type"]]["attributes"]}
        for attr, spec in (rule.get("attributes") or {}).items():
            if attr not in allowed:
                raise MappingError(f"{path}: events[{i}] {rule['type']} has no attribute {attr!r}")
            targets = list((spec.get("map") or {}).values()) + ([spec["value"]] if "value" in spec else [])
            bad = [v for v in targets if v not in allowed[attr]]
            if bad:
                raise MappingError(f"{path}: events[{i}] {rule['type']}.{attr}: {bad} not in {allowed[attr]}")
    return m


def _glob(pattern: str, text: str) -> bool:
    return fnmatch.fnmatch(text.lower(), pattern.lower())


def _matches(rule: dict, tag: RawTag) -> bool:
    cond = rule.get("match") or {}
    if "code" in cond and not _glob(cond["code"], tag.code):
        return False
    if "code_regex" in cond and not re.search(cond["code_regex"], tag.code, re.I):
        return False
    for group, pattern in (cond.get("label") or {}).items():
        if not any(_glob(pattern, t) for t in tag.labels.get(group, [])):
            return False
    return True


def _find_team(tag: RawTag, teams: dict[str, list[str]]) -> str | None:
    """Team whose name appears in the tag's code or labels; None if neither or both appear."""
    hits = set()
    for side in ("home", "away"):
        for name in teams.get(side) or []:
            if any(re.search(rf"(?<![a-z]){re.escape(name.lower())}(?![a-z])", t.lower()) for t in tag.texts()):
                hits.add(side)
    return hits.pop() if len(hits) == 1 else None


def _attr_value(spec: dict, tag: RawTag) -> tuple[str | None, str | None]:
    """(mapped value, raw text that didn't map)."""
    if "value" in spec:
        return spec["value"], None
    raw = None
    if "from_label" in spec:
        group = spec["from_label"]
        texts = [t for g, ts in tag.labels.items() if group == "*" or g.lower() == group.lower() for t in ts]
        mp = {k.lower(): v for k, v in (spec.get("map") or {}).items()}
        for t in texts:
            if t.lower() in mp:
                return mp[t.lower()], None
        raw = texts[0] if texts and group != "*" else None
    if "from_code" in spec:
        m = re.search(spec["from_code"], tag.code, re.I)
        if m:
            text = (m.group(1) if m.groups() else m.group(0)).lower()
            mp = {k.lower(): v for k, v in (spec.get("map") or {}).items()}
            if text in mp:
                return mp[text], None
            raw = text
    return None, raw


def apply(tags: list[RawTag], mapping: dict, teams: dict | None = None, offset_s: float = 0.0) -> tuple[list[Imported], Report]:
    pack = taxonomy.get_pack(mapping["sport"])
    teams = teams or mapping.get("teams") or {}
    offset = float(mapping.get("time_offset_s", 0)) + offset_s
    report, out = Report(total=len(tags)), []
    for tag in tags:
        if any(_glob(p, tag.code) for p in mapping.get("ignore") or []):
            report.ignored += 1
            continue
        rule = next((r for r in mapping["events"] if _matches(r, tag)), None)
        if rule is None:
            report.unmapped_codes[tag.code] += 1
            continue
        team = rule.get("team") or _find_team(tag, teams)
        if team and rule.get("team_means") == "lost":
            team = "away" if team == "home" else "home"
        if team is None:
            report.no_team[rule["type"]] += 1
        attrs = {}
        for attr, spec in (rule.get("attributes") or {}).items():
            value, raw = _attr_value(spec, tag)
            if value:
                attrs[attr] = value
            elif raw:
                report.unmapped_values.setdefault(f"{rule['type']}.{attr}", Counter())[raw] += 1
        t0 = round(tag.t_start + offset, 2)
        t1 = round((tag.t_end if tag.t_end is not None else tag.t_start) + offset, 2)
        try:
            if t0 < 0:
                raise taxonomy.TaxonomyError(f"time {t0}s is before the start of the video (check offsets)")
            taxonomy.validate_event(pack["sport"], rule["type"], team, attrs)
        except taxonomy.TaxonomyError as exc:
            report.invalid.append((tag.row, tag.code, str(exc)))
            continue
        out.append(Imported(rule["type"], team, t0, max(t0, t1), attrs, tag.code, tag.row))
        report.mapped += 1
    return out, report


# Keyword guesses for the starter mapping written by `inspect`.
_GUESS = [("kickout", "kick ?out|kick-out|restart"), ("shot", "shot|score|point|goal|wide"),
          ("turnover", "turnover|t/?o\\b|possession (won|lost)"), ("free", "\\bfree\\b|foul")]


def starter_mapping(tags: list[RawTag], sport: str) -> str:
    """A mapping YAML to start from: every code, guessed types, every label group and value."""
    pack = taxonomy.get_pack(sport)
    codes = Counter(t.code for t in tags)
    groups: dict[str, Counter] = {}
    for t in tags:
        for g, ts in t.labels.items():
            groups.setdefault(g or "(no group)", Counter()).update(ts)
    lines = [f"sport: {sport}", "time_offset_s: 0", "teams:", "  home: [Home]   # TODO names used for the home team in codes/labels",
             "  away: [Away]", "ignore: []", "events:"]
    for code, n in codes.most_common():
        guess = next((typ for typ, rx in _GUESS if typ in pack["_types"] and re.search(rx, code, re.I)), None)
        lines.append(f"  - match: {{code: {code!r}}}   # {n} tag{'' if n == 1 else 's'}")
        lines.append(f"    type: {guess or 'TODO'}" + ("" if guess else f"   # one of {list(pack['_types'])}"))
    lines.append("# Label groups and values seen (map them under each rule's attributes):")
    for g, vals in groups.items():
        lines.append(f"#   {g}: " + ", ".join(f"{v} ({n})" for v, n in vals.most_common(25)))
    return "\n".join(lines) + "\n"
