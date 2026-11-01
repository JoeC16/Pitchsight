"""Sportscode-style XML (also exported by Nacsport, LongoMatch and others):

    <file><ALL_INSTANCES>
      <instance><ID>1</ID><start>12.4</start><end>20.0</end><code>Kickout</code>
        <label><group>Team</group><text>Home</text></label>
        <label><text>Long</text></label>
      </instance> ...
"""
import xml.etree.ElementTree as ET
from pathlib import Path

from . import RawTag, parse_time


def _text(el, name: str) -> str | None:
    child = el.find(name)
    return child.text.strip() if child is not None and child.text else None


def read_xml(path: Path) -> list[RawTag]:
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"{path}: not valid XML ({exc})") from None
    tags = []
    for n, inst in enumerate(root.iter("instance"), start=1):
        start, code = _text(inst, "start"), _text(inst, "code")
        if start is None or code is None:
            continue
        labels: dict[str, list[str]] = {}
        for lab in inst.findall("label"):
            text = _text(lab, "text")
            if text:
                labels.setdefault(_text(lab, "group") or "", []).append(text)
        for extra in ("free_text", "notes"):
            if (t := _text(inst, extra)):
                labels.setdefault(extra, []).append(t)
        end = _text(inst, "end")
        tags.append(RawTag(parse_time(start), parse_time(end) if end else None, code, labels, n))
    if not tags:
        raise ValueError(f"{path}: no <instance> elements with <start> and <code> found")
    return sorted(tags, key=lambda t: t.t_start)
