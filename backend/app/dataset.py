"""Datasets of tagged games: import tags, split by ground/team, run a detector, evaluate.

Manifest CSV, one row per game (paths relative to the manifest):

    game_id,video,tags,mapping,home_names,away_names,offset_s,ground,home_team,away_team,pitch_setup,split
    g001,videos/g001.mp4,tags/g001.xml,,St Marys,Ballyboden,0,Parnell Park,St Marys,Ballyboden,,

    mapping      per-game mapping YAML (else --mapping); home_names/away_names: names used in the
                 tags, '|'-separated (override the mapping's teams); offset_s: seconds added to tag
                 times (e.g. when the video was trimmed); pitch_setup: JSON file with a saved
                 calibration (Match.pitch_setup) for the real detector; split: train | val | test.

    python -m app.dataset import   MANIFEST --mapping M.yaml --truth-dir truth/
    python -m app.dataset split    MANIFEST --by ground [--ratios 70,15,15] [--seed 1] [--write]
    python -m app.dataset run      MANIFEST --split val --detector stub --pred-dir preds/
    python -m app.dataset evaluate MANIFEST --split test --truth-dir truth/ --pred-dir preds/
"""
import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from . import evaluation, taxonomy
from .tags import read as read_tags
from .tags.__main__ import write_truth_csv
from .tags.mapping import Report, apply, load_mapping

SPLITS = ("train", "val", "test")


def load_manifest(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = [{k.strip(): (v or "").strip() for k, v in r.items() if k} for r in csv.DictReader(f)]
    ids = Counter(r.get("game_id") for r in rows)
    dupes = [g for g, n in ids.items() if n > 1 or not g]
    if dupes:
        raise ValueError(f"{path}: game_id must be unique and non-empty (problem ids: {dupes})")
    return rows


def save_manifest(rows: list[dict], path: Path) -> None:
    cols = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def _rel(manifest: Path, value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else manifest.parent / p


def grouped_split(rows: list[dict], by: str, ratios=(70, 15, 15), seed: int = 1) -> dict[str, str]:
    """{game_id: split}. All games sharing a value of `by` (e.g. ground) land in the same split,
    so the test set holds grounds/teams the model never saw. Deterministic for a given seed."""
    groups: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        groups[(r.get(by) or f"__{r['game_id']}") if by != "game" else r["game_id"]].append(r["game_id"])
    order = sorted(groups, key=lambda g: hashlib.sha256(f"{seed}:{g}".encode()).hexdigest())
    total, target = len(rows), [x / sum(ratios) * len(rows) for x in ratios]
    counts, out = [0, 0, 0], {}
    # Fill the smallest splits first (test, val) so they aren't starved by big groups; train takes the rest.
    for g in order:
        need = [target[i] - counts[i] for i in range(3)]
        i = 2 if need[2] > 0 else 1 if need[1] > 0 else 0
        for gid in groups[g]:
            out[gid] = SPLITS[i]
        counts[i] += len(groups[g])
    assert sum(counts) == total
    return out


def cmd_import(args) -> int:
    rows = load_manifest(args.manifest)
    args.truth_dir.mkdir(parents=True, exist_ok=True)
    total, per_type, problems = Report(), Counter(), []
    for r in rows:
        mapping = load_mapping(_rel(args.manifest, r["mapping"]) if r.get("mapping") else args.mapping)
        teams = None
        if r.get("home_names") or r.get("away_names"):
            teams = {"home": [n for n in r.get("home_names", "").split("|") if n],
                     "away": [n for n in r.get("away_names", "").split("|") if n]}
        try:
            tags = read_tags(_rel(args.manifest, r["tags"]), csv_columns=mapping.get("csv_columns"))
        except (ValueError, OSError) as exc:
            problems.append(f"{r['game_id']}: {exc}")
            continue
        events, rep = apply(tags, mapping, teams=teams, offset_s=float(r.get("offset_s") or 0))
        write_truth_csv(events, taxonomy.get_pack(mapping["sport"]), args.truth_dir / f"{r['game_id']}.csv",
                        Path(r.get("video", "")).name)
        per_type.update(e.type for e in events)
        if not rep.ok() or rep.no_team:
            problems.append(f"{r['game_id']}: {rep.mapped}/{rep.total} mapped, {sum(rep.unmapped_codes.values())} unmapped, "
                            f"{len(rep.invalid)} invalid, {sum(rep.no_team.values())} without team")
        total.total += rep.total; total.mapped += rep.mapped; total.ignored += rep.ignored  # noqa: E702
        total.unmapped_codes.update(rep.unmapped_codes)
        total.no_team.update(rep.no_team)
        total.invalid += [(row, f"{r['game_id']}: {code}", why) for row, code, why in rep.invalid]
        for k, v in rep.unmapped_values.items():
            total.unmapped_values.setdefault(k, Counter()).update(v)
    print(f"{len(rows)} games")
    print(total.text())
    print("Events per type: " + ", ".join(f"{t} {n}" for t, n in per_type.most_common()))
    if problems:
        print(f"\n{len(problems)} game(s) need attention:")
        print("\n".join("  " + p for p in problems))
    return 1 if args.strict and problems else 0


def cmd_split(args) -> int:
    rows = load_manifest(args.manifest)
    ratios = tuple(int(x) for x in args.ratios.split(","))
    assignment = grouped_split(rows, args.by, ratios, args.seed)
    for r in rows:
        r["split"] = assignment[r["game_id"]]
    by_split = Counter(r["split"] for r in rows)
    print(f"Split by {args.by}: " + ", ".join(f"{s} {by_split[s]}" for s in SPLITS))
    if args.by != "game":
        for s in SPLITS:
            vals = sorted({r.get(args.by) or "?" for r in rows if r["split"] == s})
            print(f"  {s}: {', '.join(vals[:12])}{' ...' if len(vals) > 12 else ''}")
    if args.write:
        save_manifest(rows, args.manifest)
        print(f"split column written to {args.manifest}")
    return 0


def _in_split(rows, split):
    return [r for r in rows if not split or r.get("split") == split]


def cmd_run(args) -> int:
    from .pipeline import get_detector
    from .pipeline.interfaces import MatchContext
    from .pipeline.probe import probe

    rows = _in_split(load_manifest(args.manifest), args.split)
    detector = get_detector(args.detector)
    args.pred_dir.mkdir(parents=True, exist_ok=True)
    failed = 0
    for r in rows:
        video = _rel(args.manifest, r["video"])
        try:
            info = probe(video)
            setup = json.loads(_rel(args.manifest, r["pitch_setup"]).read_text()) if r.get("pitch_setup") else None
            pack = taxonomy.get_pack(args.sport)
            drafts = detector.detect(video, info, pack, lambda p: None, MatchContext(args.sport, pitch_setup=setup))
        except Exception as exc:  # noqa: BLE001 - one bad game shouldn't stop a 100-game run
            print(f"{r['game_id']}: FAILED {exc}")
            failed += 1
            continue
        valid = []
        for d in drafts:
            try:
                taxonomy.validate_event(args.sport, d.type, d.team, d.attributes)
                valid.append(d)
            except taxonomy.TaxonomyError:
                pass
        for d in valid:
            d.code, d.row = f"{detector.name} confidence {d.confidence}", 0
        write_truth_csv(valid, taxonomy.get_pack(args.sport), args.pred_dir / f"{r['game_id']}.csv", video.name)
        print(f"{r['game_id']}: {len(valid)} events")
    if detector.name == "stub":
        print("DEMO: the stub detector's events are simulated; evaluating them measures nothing.")
    return 1 if failed else 0


def cmd_evaluate(args) -> int:
    rows = _in_split(load_manifest(args.manifest), args.split)
    results, missing = [], []
    for r in rows:
        truth_f, pred_f = args.truth_dir / f"{r['game_id']}.csv", args.pred_dir / f"{r['game_id']}.csv"
        if not truth_f.exists() or not pred_f.exists():
            missing.append(r["game_id"])
            continue
        res = evaluation.evaluate(evaluation.load_csv(pred_f), evaluation.load_csv(truth_f), None, args.tolerance)
        res["game_id"] = r["game_id"]
        results.append(res)
    if not results:
        print("No games with both truth and predictions" + (f" (missing: {missing})" if missing else ""))
        return 1
    agg = evaluation.aggregate(results)
    agg["split"] = args.split
    if args.json:
        print(json.dumps({"aggregate": agg, "games": results}, indent=2))
        return 0
    print(evaluation.format_report(agg, f"{agg['games']} games, split: {args.split or 'all'}"))
    print("\nPer game (overall precision / recall):")
    pct = lambda v: "  - " if v is None else f"{v * 100:3.0f}%"  # noqa: E731
    for res in sorted(results, key=lambda x: (x["overall"]["recall"] or 0)):
        o = res["overall"]
        print(f"  {res['game_id']:<16} {pct(o['precision'])} / {pct(o['recall'])}   (tp {o['tp']}, fp {o['fp']}, fn {o['fn']})")
    if missing:
        print(f"\nSkipped (no truth or predictions): {', '.join(missing)}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.dataset", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("import", help="convert every game's tags to ground-truth CSV")
    p.add_argument("manifest", type=Path); p.add_argument("--mapping", type=Path)  # noqa: E702
    p.add_argument("--truth-dir", type=Path, required=True); p.add_argument("--strict", action="store_true")  # noqa: E702
    p = sub.add_parser("split", help="assign train/val/test, grouped so a ground/team is in one split only")
    p.add_argument("manifest", type=Path); p.add_argument("--by", default="ground")  # noqa: E702
    p.add_argument("--ratios", default="70,15,15"); p.add_argument("--seed", type=int, default=1)  # noqa: E702
    p.add_argument("--write", action="store_true", help="save the split column into the manifest")
    p = sub.add_parser("run", help="run a detector over the games' videos, write prediction CSVs")
    p.add_argument("manifest", type=Path); p.add_argument("--split", choices=SPLITS)  # noqa: E702
    p.add_argument("--detector", default="real"); p.add_argument("--sport", default="gaa_football")  # noqa: E702
    p.add_argument("--pred-dir", type=Path, required=True)
    p = sub.add_parser("evaluate", help="precision/recall over many games")
    p.add_argument("manifest", type=Path); p.add_argument("--split", choices=SPLITS)  # noqa: E702
    p.add_argument("--truth-dir", type=Path, required=True); p.add_argument("--pred-dir", type=Path, required=True)  # noqa: E702
    p.add_argument("--tolerance", type=float, default=3.0); p.add_argument("--json", action="store_true")  # noqa: E702
    args = ap.parse_args(argv)
    if args.cmd == "import" and not args.mapping:
        rows = load_manifest(args.manifest)
        if any(not r.get("mapping") for r in rows):
            ap.error("give --mapping (some games have no mapping column)")
    try:
        return {"import": cmd_import, "split": cmd_split, "run": cmd_run, "evaluate": cmd_evaluate}[args.cmd](args)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
