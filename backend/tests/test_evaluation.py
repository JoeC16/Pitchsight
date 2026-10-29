import json
from pathlib import Path

import pytest

from app import evaluate as evaluate_cli
from app.evaluation import Item, evaluate, load_csv, match_items
from tests.test_api import fake_probe, upload  # noqa: F401

TEMPLATE = Path(__file__).resolve().parents[2] / "sample_matches" / "ground_truth_template.csv"


def test_matching_is_one_to_one_closest_first():
    truth = [Item("shot", 10.0), Item("shot", 12.0)]
    pred = [Item("shot", 11.8), Item("shot", 11.0)]
    pairs = match_items(pred, truth, 3.0)
    # 11.8 takes 12.0 (closest pair overall), leaving 11.0 for 10.0.
    assert {(p.t, g.t) for p, g in pairs} == {(11.8, 12.0), (11.0, 10.0)}
    assert match_items([Item("shot", 20.0)], [Item("shot", 10.0)], 3.0) == []
    assert match_items([Item("kickout", 10.0)], [Item("shot", 10.0)], 3.0) == []


def test_metrics_team_attributes_and_spans():
    truth = [Item("shot", 10, "home", {"outcome": "point"}), Item("shot", 50, "away", {"outcome": "wide"}),
             Item("kickout", 100, "home"), Item("shot", 500, "home")]
    pred = [Item("shot", 11, "home", {"outcome": "point"}), Item("shot", 51, "home", {"outcome": "point"}),
            Item("kickout", 200, "home"), Item("shot", 499, "home")]
    r = evaluate(pred, truth, spans=[(0, 300)])
    assert r["checked_seconds"] == 300
    sub = lambda d, keys: {k: d[k] for k in keys}  # noqa: E731
    keys = ["tp", "fp", "fn", "precision", "recall", "team_agreement"]
    assert sub(r["types"]["shot"], keys) == dict(zip(keys, [2, 0, 0, 1.0, 1.0, 0.5]))  # shot at 500 is outside the span
    assert sub(r["types"]["kickout"], keys) == dict(zip(keys, [0, 1, 1, 0.0, 0.0, None]))
    assert r["attributes"]["shot"]["outcome"] == {"correct": 1, "total": 2, "accuracy": 0.5}
    assert r["overall"]["tp"] == 2 and r["overall"]["fp"] == 1 and r["overall"]["fn"] == 1
    assert evaluate([], [])["overall"]["precision"] is None  # nothing to score, not 0%


def test_load_csv_template_and_errors(tmp_path):
    items = load_csv(TEMPLATE)
    assert [i.type for i in items] == ["kickout", "turnover", "shot"]
    assert items[0].t == 62.0 and items[0].team == "home"
    assert items[0].attributes == {"outcome": "retained_clean", "length": "long", "target": "left"}
    assert load_csv(TEMPLATE, video_file="other.mp4") == []
    bad = tmp_path / "bad.csv"
    bad.write_text("type,team,t_start\nshot,home,1:02\n")
    with pytest.raises(ValueError, match="seconds into the video"):
        load_csv(bad)


def test_runs_are_saved(client, match, fake_probe):  # noqa: F811
    upload(client, match["id"])
    runs = client.get(f"/api/matches/{match['id']}/runs").json()
    events = client.get(f"/api/matches/{match['id']}/events").json()
    assert len(runs) == 1 and runs[0]["detector"] == "stub" and runs[0]["event_count"] == len(events)


def test_verify_a_stretch_and_score_it(client, match, fake_probe):  # noqa: F811
    m = match["id"]
    upload(client, m)
    base = f"/api/matches/{m}/events"
    span = {"t_start": 0, "t_end": 600}
    assert client.get(f"/api/matches/{m}/evaluation").status_code == 409  # nothing checked yet
    r = client.post(f"/api/matches/{m}/verified-spans", json=span)
    assert r.status_code == 409 and "unreviewed" in r.json()["detail"]

    inside = [e for e in client.get(base).json() if e["t_start"] <= 600]
    assert len(inside) >= 5
    wrong, team_fix, rest = inside[0], inside[1], inside[2:]
    client.delete(f"/api/events/{wrong['id']}")                                      # false positive
    flipped = "away" if team_fix["team"] == "home" else "home"
    client.patch(f"/api/events/{team_fix['id']}", json={"team": flipped})            # right moment, wrong team
    for e in rest:
        client.patch(f"/api/events/{e['id']}", json={"reviewed": True})              # confirmed
    # A miss: a type/time with no detection of that type within 3s.
    miss_t = next(t for t in range(5, 600, 7)
                  if not any(e["type"] == "free" and abs(e["t_start"] - t) <= 3 for e in inside))
    client.post(base, json={"type": "free", "team": "home", "t_start": miss_t, "t_end": miss_t + 2})

    assert client.post(f"/api/matches/{m}/verified-spans", json=span).status_code == 201
    r = client.get(f"/api/matches/{m}/evaluation").json()
    assert r["demo"] is True and r["detector"] == "stub" and r["checked_seconds"] == 600
    assert r["overall"]["tp"] == len(inside) - 1 and r["overall"]["fp"] == 1 and r["overall"]["fn"] == 1
    team_scores = [t["team_agreement"] for t in r["types"].values() if t["team_agreement"] is not None]
    assert min(team_scores) < 1.0  # the flipped team shows up as a team error

    # CLI gives the same numbers.
    import contextlib
    import io
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert evaluate_cli.main(["--match", str(m), "--json"]) == 0
    assert json.loads(out.getvalue())["overall"] == r["overall"]


def test_span_validation_and_delete(client, match, fake_probe):  # noqa: F811
    m = match["id"]
    assert client.post(f"/api/matches/{m}/verified-spans", json={"t_start": 10, "t_end": 5}).status_code == 422
    span = client.post(f"/api/matches/{m}/verified-spans", json={"t_start": 0, "t_end": 1}).json()  # no events yet
    assert [s["id"] for s in client.get(f"/api/matches/{m}/verified-spans").json()] == [span["id"]]
    assert client.delete(f"/api/matches/{m}/verified-spans/{span['id']}").status_code == 204
    assert client.get(f"/api/matches/{m}/verified-spans").json() == []


def test_reprocess_does_not_duplicate_reviewed_events(client, match, fake_probe):  # noqa: F811
    m = match["id"]
    upload(client, m)
    base = f"/api/matches/{m}/events"
    before = client.get(base).json()
    for e in before[:10]:
        client.patch(f"/api/events/{e['id']}", json={"reviewed": True})
    client.post(f"/api/matches/{m}/reprocess")
    after = client.get(base).json()
    assert len(after) == len(before)  # same stub output: the 10 reviewed ones aren't added twice
    assert len(client.get(f"/api/matches/{m}/runs").json()) == 2


def test_evaluation_routes_are_club_scoped(client, other_club, match, fake_probe):  # noqa: F811
    m = match["id"]
    upload(client, m)
    span = client.post(f"/api/matches/{m}/verified-spans", json={"t_start": 0, "t_end": 1}).json()
    for method, path in [("GET", f"/api/matches/{m}/runs"), ("GET", f"/api/matches/{m}/verified-spans"),
                         ("POST", f"/api/matches/{m}/verified-spans"), ("GET", f"/api/matches/{m}/evaluation"),
                         ("DELETE", f"/api/matches/{m}/verified-spans/{span['id']}")]:
        assert other_club.request(method, path, json={"t_start": 0, "t_end": 1}).status_code == 404
