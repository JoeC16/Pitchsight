"""Score detected events against ground truth: precision and recall per event type.

Ground truth comes from either
  - the app: reviewed and manual events inside the match's verified spans (a coach has checked
    those stretches end to end), or
  - a hand-tagged CSV in sample_matches/ground_truth_template.csv format.

A detection matches a true event when the type is the same and their start times are within
`tolerance_s`. Matching is one-to-one, closest pairs first. Team and attributes are scored
separately on matched pairs, so a right-moment-wrong-team detection shows up as a team error
rather than as both a false positive and a miss.
"""
import csv
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

Span = tuple[float, float]

# Columns of the ground-truth CSV that aren't attributes.
CSV_FIXED = {"video_file", "type", "team", "period", "t_start", "t_end", "notes"}


@dataclass
class Item:
    type: str
    t: float  # start time, seconds into the video
    team: str | None = None
    attributes: dict[str, str] = field(default_factory=dict)


def in_spans(t: float, spans: list[Span] | None) -> bool:
    return spans is None or any(a <= t <= b for a, b in spans)


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 3) if den else None


def _scores(tp: int, fp: int, fn: int) -> dict:
    p, r = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
    f1 = round(2 * p * r / (p + r), 3) if p and r else None
    return {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r, "f1": f1}


def match_items(pred: list[Item], truth: list[Item], tolerance_s: float) -> list[tuple[Item, Item]]:
    """One-to-one matching of predictions to truth: same type, |dt| <= tolerance, closest first."""
    candidates = sorted(
        (abs(p.t - g.t), i, j)
        for i, p in enumerate(pred)
        for j, g in enumerate(truth)
        if p.type == g.type and abs(p.t - g.t) <= tolerance_s
    )
    used_p, used_g, pairs = set(), set(), []
    for _, i, j in candidates:
        if i not in used_p and j not in used_g:
            used_p.add(i)
            used_g.add(j)
            pairs.append((pred[i], truth[j]))
    return pairs


def evaluate(pred: list[Item], truth: list[Item], spans: list[Span] | None = None, tolerance_s: float = 3.0) -> dict:
    """Metrics per event type and overall. With `spans`, only events inside them count."""
    pred = [p for p in pred if in_spans(p.t, spans)]
    truth = [g for g in truth if in_spans(g.t, spans)]
    pairs = match_items(pred, truth, tolerance_s)

    by_type: dict[str, dict] = defaultdict(lambda: {"pred": 0, "truth": 0, "pairs": []})
    for p in pred:
        by_type[p.type]["pred"] += 1
    for g in truth:
        by_type[g.type]["truth"] += 1
    for p, g in pairs:
        by_type[g.type]["pairs"].append((p, g))

    types, attributes = {}, {}
    for t, d in sorted(by_type.items()):
        tp = len(d["pairs"])
        row = _scores(tp, d["pred"] - tp, d["truth"] - tp)
        row["mean_offset_s"] = round(sum(abs(p.t - g.t) for p, g in d["pairs"]) / tp, 2) if tp else None
        with_team = [(p, g) for p, g in d["pairs"] if g.team]
        row["team_agreement"] = _ratio(sum(p.team == g.team for p, g in with_team), len(with_team))
        types[t] = row

        attr: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # attribute -> [correct, total]
        for p, g in d["pairs"]:
            for k, v in g.attributes.items():
                attr[k][1] += 1
                attr[k][0] += p.attributes.get(k) == v
        if attr:
            attributes[t] = {k: {"correct": c, "total": n, "accuracy": _ratio(c, n)} for k, (c, n) in sorted(attr.items())}

    tp = len(pairs)
    return {
        "tolerance_s": tolerance_s,
        "checked_seconds": round(sum(b - a for a, b in spans), 1) if spans is not None else None,
        "overall": _scores(tp, len(pred) - tp, len(truth) - tp),
        "types": types,
        "attributes": attributes,
    }


def load_csv(path: Path, video_file: str | None = None) -> list[Item]:
    """Read events from a CSV in ground_truth_template.csv format (also usable for predictions).
    `video_file` keeps only rows for that file, when the CSV covers several matches."""
    items = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for n, row in enumerate(csv.DictReader(f), start=2):
            if video_file and row.get("video_file") and row["video_file"] != video_file:
                continue
            if not (row.get("type") or "").strip():
                continue
            try:
                t = float(row["t_start"])
            except (KeyError, ValueError):
                raise ValueError(f"{path}:{n}: t_start must be seconds into the video, got {row.get('t_start')!r}") from None
            attrs = {k: v.strip() for k, v in row.items() if k and k not in CSV_FIXED and v and v.strip()}
            items.append(Item(type=row["type"].strip(), t=t, team=(row.get("team") or "").strip() or None, attributes=attrs))
    return items


def format_report(result: dict, title: str = "") -> str:
    """Plain-text table of an evaluate() result."""
    pct = lambda v: "  -  " if v is None else f"{v * 100:4.0f}%"  # noqa: E731
    lines = [title] if title else []
    if result.get("checked_seconds") is not None:
        lines.append(f"Checked video: {result['checked_seconds'] / 60:.1f} min   tolerance: ±{result['tolerance_s']}s")
    lines.append(f"{'type':<12}{'found':>6}{'missed':>7}{'false':>6}{'precision':>11}{'recall':>8}{'team ok':>9}")
    for t, r in result["types"].items():
        lines.append(f"{t:<12}{r['tp']:>6}{r['fn']:>7}{r['fp']:>6}{pct(r['precision']):>11}{pct(r['recall']):>8}{pct(r['team_agreement']):>9}")
    o = result["overall"]
    lines.append(f"{'ALL':<12}{o['tp']:>6}{o['fn']:>7}{o['fp']:>6}{pct(o['precision']):>11}{pct(o['recall']):>8}")
    for t, attrs in result["attributes"].items():
        lines.append(f"  {t} attributes: " + ", ".join(f"{k} {pct(a['accuracy']).strip()} of {a['total']}" for k, a in attrs.items()))
    return "\n".join(lines)


# ---------- from the app's database ----------

def run_items(run) -> list[Item]:
    return [Item(type=d["type"], t=d["t_start"], team=d.get("team"), attributes=d.get("attributes") or {})
            for d in run.drafts]


def verified_truth(db, match) -> tuple[list[Item], list[Span]]:
    """Reviewed events (incl. manual tags) and the spans they are complete for."""
    from sqlalchemy import select

    from .models import Event, VerifiedSpan

    spans = [(s.t_start, s.t_end) for s in
             db.scalars(select(VerifiedSpan).where(VerifiedSpan.match_id == match.id).order_by(VerifiedSpan.t_start))]
    events = db.scalars(select(Event).where(Event.match_id == match.id, Event.reviewed.is_(True))).all()
    return [Item(type=e.type, t=e.t_start, team=e.team, attributes=e.attributes or {}) for e in events], spans
