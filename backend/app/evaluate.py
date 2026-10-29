"""Evaluate detection against ground truth from the command line.

    # A match in the app, scored against the stretches a coach marked as fully checked:
    python -m app.evaluate --match 3 [--run 7] [--tolerance 3]

    # Against a hand-tagged CSV (sample_matches/ground_truth_template.csv format):
    python -m app.evaluate --match 3 --truth-csv truth.csv [--video-file match1.mp4]

    # Two CSVs, no database (e.g. detections exported from elsewhere):
    python -m app.evaluate --pred-csv detections.csv --truth-csv truth.csv

Add --json for machine-readable output.
"""
import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select

from . import evaluation
from .db import SessionLocal
from .migrate import upgrade_db
from .models import DetectionRun, Match


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.evaluate", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--match", type=int, help="match id in the app")
    ap.add_argument("--run", type=int, help="detection run id (default: the match's latest)")
    ap.add_argument("--pred-csv", type=Path, help="predictions as CSV instead of a run")
    ap.add_argument("--truth-csv", type=Path, help="ground truth as CSV instead of verified spans")
    ap.add_argument("--video-file", help="only CSV rows with this video_file")
    ap.add_argument("--tolerance", type=float, default=3.0, help="seconds (default 3)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.match is None and args.pred_csv is None:
        ap.error("give --match or --pred-csv")
    if args.match is None and args.truth_csv is None:
        ap.error("--pred-csv needs --truth-csv")

    title, spans, demo = "", None, False
    if args.pred_csv:
        pred, title = evaluation.load_csv(args.pred_csv, args.video_file), f"Predictions: {args.pred_csv}"
    if args.truth_csv:
        truth = evaluation.load_csv(args.truth_csv, args.video_file)

    if args.match is not None:
        upgrade_db()
        with SessionLocal() as db:
            match = db.get(Match, args.match)
            if match is None:
                ap.error(f"no match {args.match}")
            if not args.pred_csv:
                q = select(DetectionRun).where(DetectionRun.match_id == match.id)
                q = q.where(DetectionRun.id == args.run) if args.run else q.order_by(DetectionRun.id.desc())
                run = db.scalars(q).first()
                if run is None:
                    ap.error("no detection run for that match")
                pred, demo = evaluation.run_items(run), run.detector == "stub"
                title = f"Match {match.id} '{match.title}', run {run.id} ({run.detector})"
            if not args.truth_csv:
                truth, spans = evaluation.verified_truth(db, match)
                if not spans:
                    ap.error("no verified spans for that match yet - mark checked stretches in the app, or pass --truth-csv")

    result = evaluation.evaluate(pred, truth, spans, args.tolerance)
    result["demo"] = demo
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if demo:
            print("DEMO DATA: these detections are simulated (stub detector); the scores mean nothing.\n")
        print(evaluation.format_report(result, title))
    return 0


if __name__ == "__main__":
    sys.exit(main())
