"""python -m app.tags inspect FILE [--sport gaa_football] [--starter mapping.yaml]
   python -m app.tags convert FILE --mapping M.yaml -o truth.csv [--video-file NAME] [--offset S]
"""
import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

from .. import taxonomy
from . import read
from .mapping import MappingError, apply, load_mapping, starter_mapping


def truth_columns(pack: dict) -> list[str]:
    attrs = []
    for et in pack["event_types"]:
        for a in et["attributes"]:
            if a["id"] not in attrs:
                attrs.append(a["id"])
    return ["video_file", "type", "team", "period", "t_start", "t_end"] + attrs + ["notes"]


def write_truth_csv(events, pack: dict, path: Path, video_file: str = "") -> None:
    cols = truth_columns(pack)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for e in events:
            w.writerow({"video_file": video_file, "type": e.type, "team": e.team or "", "period": "",
                        "t_start": e.t_start, "t_end": e.t_end, **e.attributes, "notes": f"{e.code} (row {e.row})"})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.tags")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ins = sub.add_parser("inspect", help="list codes, labels and values in a tag file")
    ins.add_argument("file", type=Path)
    ins.add_argument("--format", choices=["xml", "csv"])
    ins.add_argument("--sport", default="gaa_football")
    ins.add_argument("--starter", type=Path, help="write a starter mapping YAML here")
    cv = sub.add_parser("convert", help="map a tag file to ground-truth CSV")
    cv.add_argument("file", type=Path)
    cv.add_argument("--mapping", type=Path, required=True)
    cv.add_argument("-o", "--out", type=Path, required=True)
    cv.add_argument("--format", choices=["xml", "csv"])
    cv.add_argument("--video-file", default="")
    cv.add_argument("--offset", type=float, default=0.0, help="seconds added to every tag (on top of the mapping's)")
    cv.add_argument("--strict", action="store_true", help="exit 1 if anything didn't map")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "inspect":
            tags = read(args.file, args.format)
            print(f"{len(tags)} tags, {tags[0].t_start:.1f}s to {tags[-1].t_start:.1f}s")
            print("Codes:")
            for code, n in Counter(t.code for t in tags).most_common():
                print(f"  {n:>5}  {code}")
            groups: dict[str, Counter] = {}
            for t in tags:
                for g, ts in t.labels.items():
                    groups.setdefault(g or "(no group)", Counter()).update(ts)
            for g, vals in groups.items():
                print(f"Label group {g!r}: " + ", ".join(f"{v} ({n})" for v, n in vals.most_common(30)))
            if args.starter:
                args.starter.write_text(starter_mapping(tags, args.sport))
                print(f"Starter mapping written to {args.starter}")
            return 0

        mapping = load_mapping(args.mapping)
        csv_cols = mapping.get("csv_columns")
        events, report = apply(read(args.file, args.format, csv_cols), mapping, offset_s=args.offset)
        write_truth_csv(events, taxonomy.get_pack(mapping["sport"]), args.out, args.video_file)
        print(report.text())
        print(f"{len(events)} events written to {args.out}")
        return 1 if args.strict and not report.ok() else 0
    except (ValueError, MappingError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
