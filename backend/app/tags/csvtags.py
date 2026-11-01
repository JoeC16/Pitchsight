"""Spreadsheet exports. Columns are found by common header names unless given explicitly:

    csv_columns: {start: "Start time", end: "End", code: "Event", labels: ["Outcome", "Team"]}

Unlisted columns other than start/end/code become labels, grouped by their header.
"""
import csv
import io
from pathlib import Path

from . import RawTag, parse_time

GUESS = {
    "start": ["start", "start time", "start_time", "time", "timestamp", "position", "in", "t_start", "clip start"],
    "end": ["end", "end time", "end_time", "out", "t_end", "clip end", "stop"],
    "code": ["code", "event", "name", "category", "action", "type", "row", "button"],
}


def _sniff(sample: str) -> csv.Dialect:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        return csv.excel


def read_csv(path: Path, columns: dict | None = None) -> list[RawTag]:
    raw = Path(path).read_bytes().decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(raw), dialect=_sniff(raw[:4096]))
    headers = reader.fieldnames or []
    lower = {h.strip().lower(): h for h in headers if h}
    cols = dict(columns or {})
    for key, names in GUESS.items():
        if key not in cols:
            cols[key] = next((lower[n] for n in names if n in lower), None)
    if not cols.get("start") or not cols.get("code"):
        raise ValueError(f"{path}: can't find the start-time and event columns in {headers}; "
                         "name them under csv_columns in the mapping")
    label_cols = cols.get("labels") or [h for h in headers if h and h not in (cols["start"], cols.get("end"), cols["code"])]
    tags = []
    for n, row in enumerate(reader, start=2):
        start, code = (row.get(cols["start"]) or "").strip(), (row.get(cols["code"]) or "").strip()
        if not start or not code:
            continue
        try:
            t0 = parse_time(start)
            t1 = parse_time(row[cols["end"]]) if cols.get("end") and (row.get(cols["end"]) or "").strip() else None
        except ValueError as exc:
            raise ValueError(f"{path}:{n}: {exc}") from None
        labels = {h: [row[h].strip()] for h in label_cols if (row.get(h) or "").strip()}
        tags.append(RawTag(t0, t1, code, labels, n))
    return sorted(tags, key=lambda t: t.t_start)
