import csv
import shutil
import subprocess
from pathlib import Path

import pytest

from app import dataset, evaluation
from app.evaluation import Item, aggregate, evaluate

FIX = Path(__file__).parent / "fixtures"


def test_aggregate_sums_counts_then_recomputes_ratios():
    a = evaluate([Item("shot", 10, "home"), Item("shot", 50, "away")], [Item("shot", 11, "home")])           # tp1 fp1
    b = evaluate([Item("shot", 10, "away")], [Item("shot", 10, "home"), Item("kickout", 30, "home")])      # tp1 fn1(kickout)
    agg = aggregate([a, b])
    assert agg["games"] == 2
    assert agg["types"]["shot"] | {} == {**agg["types"]["shot"], "tp": 2, "fp": 1, "fn": 0, "precision": 0.667, "recall": 1.0}
    assert agg["types"]["shot"]["team_agreement"] == 0.5 and agg["types"]["shot"]["team_n"] == 2
    assert agg["types"]["kickout"]["fn"] == 1 and agg["overall"] == {**agg["overall"], "tp": 2, "fp": 1, "fn": 1}


def _rows(n_grounds=8, per_ground=3):
    return [{"game_id": f"g{g}{k}", "ground": f"Ground {g}", "home_team": f"Club {k}"}
            for g in range(n_grounds) for k in range(per_ground)]


def test_grouped_split_keeps_each_ground_in_one_split():
    rows = _rows()
    split = dataset.grouped_split(rows, "ground", (70, 15, 15), seed=1)
    by_ground = {}
    for r in rows:
        by_ground.setdefault(r["ground"], set()).add(split[r["game_id"]])
    assert all(len(s) == 1 for s in by_ground.values())
    counts = {s: sum(1 for v in split.values() if v == s) for s in dataset.SPLITS}
    assert counts["test"] >= 3 and counts["val"] >= 3 and counts["train"] >= 12
    assert split == dataset.grouped_split(rows, "ground", (70, 15, 15), seed=1)  # deterministic
    assert split != dataset.grouped_split(rows, "ground", (70, 15, 15), seed=2)


def test_split_by_game_and_cli_write(tmp_path, capsys):
    m = tmp_path / "manifest.csv"
    dataset.save_manifest(_rows(), m)
    assert dataset.main(["split", str(m), "--by", "ground", "--write"]) == 0
    assert "Split by ground: train" in capsys.readouterr().out
    assert {r["split"] for r in dataset.load_manifest(m)} == set(dataset.SPLITS)
    per_game = dataset.grouped_split(_rows(), "game")
    assert len(per_game) == 24


def test_manifest_requires_unique_ids(tmp_path):
    m = tmp_path / "m.csv"
    m.write_text("game_id,tags\ng1,a.xml\ng1,b.xml\n")
    with pytest.raises(ValueError, match="unique"):
        dataset.load_manifest(m)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_import_run_evaluate_end_to_end(tmp_path, capsys):
    # Three games: same tag file shape, per-game team names / offset, one with a broken tag file.
    (tmp_path / "tags").mkdir()
    xml = (FIX / "sportscode_sample.xml").read_text()
    (tmp_path / "tags" / "g1.xml").write_text(xml)
    (tmp_path / "tags" / "g2.xml").write_text(xml.replace("St Marys", "Kilmacud").replace("Ballyboden", "Crokes B"))
    (tmp_path / "tags" / "g3.xml").write_text("<file>not finished")
    video = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=600:size=64x48:rate=1",
                    "-pix_fmt", "yuv420p", str(video)], check=True)
    rows = [
        {"game_id": "g1", "video": "v.mp4", "tags": "tags/g1.xml", "home_names": "", "away_names": "", "offset_s": "0", "split": "test"},
        {"game_id": "g2", "video": "v.mp4", "tags": "tags/g2.xml", "home_names": "Kilmacud", "away_names": "Crokes B", "offset_s": "-2", "split": "test"},
        {"game_id": "g3", "video": "v.mp4", "tags": "tags/g3.xml", "home_names": "", "away_names": "", "offset_s": "0", "split": "train"},
    ]
    m = tmp_path / "manifest.csv"
    dataset.save_manifest(rows, m)
    truth = tmp_path / "truth"
    assert dataset.main(["import", str(m), "--mapping", str(FIX / "provider_mapping.yaml"), "--truth-dir", str(truth)]) == 0
    out = capsys.readouterr().out
    assert "3 games" in out and "g3: " in out and "not valid XML" in out      # broken file reported, run continues
    assert "Water break" in out                                              # unmapped codes summed over games
    g2 = evaluation.load_csv(truth / "g2.csv")
    assert g2[0].t == 60.5 and g2[0].team == "home"                          # offset and per-game names applied

    # Perfect predictions score 100%: copy truth as predictions.
    preds = tmp_path / "preds"
    preds.mkdir()
    for g in ("g1", "g2"):
        shutil.copy(truth / f"{g}.csv", preds / f"{g}.csv")
    assert dataset.main(["evaluate", str(m), "--split", "test", "--truth-dir", str(truth), "--pred-dir", str(preds)]) == 0
    out = capsys.readouterr().out
    assert "2 games, split: test" in out and "100%" in out

    # The batch runner works with any detector (the stub here: labelled demo, scores meaningless).
    stub_preds = tmp_path / "stub"
    assert dataset.main(["run", str(m), "--split", "test", "--detector", "stub", "--pred-dir", str(stub_preds)]) == 0
    out = capsys.readouterr().out
    assert "DEMO" in out and (stub_preds / "g1.csv").exists()
    with open(stub_preds / "g1.csv") as f:
        assert len(list(csv.DictReader(f))) > 50
    assert dataset.main(["evaluate", str(m), "--split", "test", "--truth-dir", str(truth), "--pred-dir", str(stub_preds), "--json"]) == 0
