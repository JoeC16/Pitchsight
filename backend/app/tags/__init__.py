"""Import event tags made in other tools (Sportscode, Nacsport, spreadsheets...).

    readers  -> list[RawTag]   (time, code, labels: whatever the tool recorded)
    mapping  -> events in the sport pack's terms (type, team, attributes), validated,
                plus a report of everything that didn't map

CLI: python -m app.tags inspect FILE | convert FILE --mapping M.yaml -o truth.csv
"""
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RawTag:
    t_start: float                     # seconds, as the tool recorded it (see mapping time_offset_s)
    t_end: float | None
    code: str                          # the tool's event/button name, e.g. "Kickout Home"
    labels: dict[str, list[str]] = field(default_factory=dict)  # group -> texts ("" = ungrouped)
    row: int = 0                       # position in the source file, for error messages

    def texts(self) -> list[str]:
        return [self.code] + [t for ts in self.labels.values() for t in ts]


_HMS = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:[.,]\d+)?)$")


def parse_time(value: str) -> float:
    """Seconds from '123.4', '123,4', '02:03', '1:02:03.5'."""
    v = str(value).strip()
    m = _HMS.match(v)
    if m:
        h, mnt, s = m.groups()
        return int(h or 0) * 3600 + int(mnt) * 60 + float(s.replace(",", "."))
    try:
        return float(v.replace(",", "."))
    except ValueError:
        raise ValueError(f"can't read time {value!r}") from None


def read(path: Path, fmt: str | None = None, csv_columns: dict | None = None) -> list[RawTag]:
    """Read a tag file. fmt: 'xml' (Sportscode/Nacsport) or 'csv'; guessed from the file if omitted."""
    path = Path(path)
    if fmt is None:
        head = path.read_bytes()[:2048].lstrip(b"\xef\xbb\xbf").lstrip()
        fmt = "xml" if head.startswith(b"<") else "csv"
    if fmt == "xml":
        from .sportscode import read_xml
        return read_xml(path)
    if fmt == "csv":
        from .csvtags import read_csv
        return read_csv(path, csv_columns)
    raise ValueError(f"unknown tag format {fmt!r} (expected xml or csv)")
