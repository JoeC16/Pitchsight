from pathlib import Path

import pytest

from app import evaluation, taxonomy
from app.tags import parse_time, read
from app.tags.__main__ import main as tags_cli
from app.tags.mapping import MappingError, apply, load_mapping, starter_mapping

FIX = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("text,seconds", [("123.4", 123.4), ("123,4", 123.4), ("02:03", 123), ("1:02:03.5", 3723.5),
                                          ("00:00:07", 7)])
def test_parse_time(text, seconds):
    assert parse_time(text) == pytest.approx(seconds)


def test_read_sportscode_xml():
    tags = read(FIX / "sportscode_sample.xml")
    assert len(tags) == 8 and tags[1].code == "Kickout St Marys"
    assert tags[1].labels == {"Outcome": ["Won Clean"], "Length": ["Long"]}
    assert tags[1].t_start == 62.5 and tags[1].t_end == 70.0


def test_read_csv_semicolons_and_clock_times():
    tags = read(FIX / "spreadsheet_sample.csv")
    assert [t.t_start for t in tags] == [62.5, 131.4, 3723.0]
    assert tags[0].code == "Kickout" and tags[0].labels == {"Team": ["St Marys"], "Outcome": ["Won Clean"]}


def test_mapping_applies_and_reports_everything_that_did_not_map():
    events, report = apply(read(FIX / "sportscode_sample.xml"), load_mapping(FIX / "provider_mapping.yaml"))
    by_t = {e.t_start: e for e in events}
    assert (by_t[62.5].type, by_t[62.5].team, by_t[62.5].attributes) == ("kickout", "home", {"outcome": "retained_clean", "length": "long"})
    # "Turnover Lost Ballyboden": Ballyboden lost it, so the pack's turnover team (the winner) is home.
    assert (by_t[131.4].type, by_t[131.4].team, by_t[131.4].attributes) == ("turnover", "home", {"zone": "middle_third"})
    assert by_t[140.2].attributes == {"outcome": "point", "source": "from_play"} and by_t[140.2].team == "away"
    assert by_t[210.0].attributes == {}                     # "Hit Crossbar" isn't mapped...
    assert report.unmapped_values == {"shot.outcome": {"Hit Crossbar": 1}}  # ...and is reported
    assert report.unmapped_codes == {"Water break": 1}
    assert report.ignored == 2 and report.no_team == {"shot": 1}           # the bare "Shot" tag
    assert report.mapped == 5 and not report.ok()
    for e in events:
        taxonomy.validate_event("gaa_football", e.type, e.team, e.attributes)


def test_per_game_team_names_and_offset():
    tags = read(FIX / "spreadsheet_sample.csv")
    mapping = {"sport": "gaa_football", "events": [{"match": {"code": "Kickout"}, "type": "kickout"},
                                                    {"match": {"code": "Turnover"}, "type": "turnover"}]}
    events, report = apply(tags, mapping, teams={"home": ["St Marys"], "away": ["Ballyboden"]}, offset_s=-60)
    assert [(e.type, e.team, e.t_start) for e in events] == [("kickout", "home", 2.5), ("turnover", "away", 71.4)]
    assert report.unmapped_codes == {"Shot": 1}


def test_negative_time_after_offset_is_invalid():
    tags = read(FIX / "spreadsheet_sample.csv")
    mapping = {"sport": "gaa_football", "events": [{"match": {"code": "*"}, "type": "free", "team": "home"}]}
    events, report = apply(tags, mapping, offset_s=-100)
    assert len(report.invalid) == 1 and "before the start" in report.invalid[0][2]


def test_team_name_matching_is_whole_word():
    from app.tags import RawTag
    from app.tags.mapping import _find_team
    teams = {"home": ["Na Fianna"], "away": ["Fianna Ard"]}
    assert _find_team(RawTag(0, None, "Shot Na Fianna"), teams) == "home"
    assert _find_team(RawTag(0, None, "Shot Fianna Ardee"), {"home": ["Fianna Ard"]}) is None


def test_bad_mappings_rejected(tmp_path):
    bad = tmp_path / "m.yaml"
    bad.write_text("sport: gaa_football\nevents:\n  - {match: {code: x}, type: penalty_shootout}\n")
    with pytest.raises(MappingError, match="isn't in the gaa_football pack"):
        load_mapping(bad)
    bad.write_text("sport: gaa_football\nevents:\n  - {match: {code: x}, type: shot, attributes: {outcome: {value: rocket}}}\n")
    with pytest.raises(MappingError, match="rocket"):
        load_mapping(bad)


def test_starter_mapping_guesses_types():
    text = starter_mapping(read(FIX / "sportscode_sample.xml"), "gaa_football")
    assert "match: {code: 'Kickout St Marys'}" in text and "type: kickout" in text
    assert "type: TODO" in text  # "Water break" can't be guessed
    assert "Result: Point (1)" in text or "Result: " in text


def test_cli_convert_produces_loadable_ground_truth(tmp_path, capsys):
    out = tmp_path / "truth.csv"
    assert tags_cli(["convert", str(FIX / "sportscode_sample.xml"), "--mapping", str(FIX / "provider_mapping.yaml"),
                     "-o", str(out), "--video-file", "g1.mp4"]) == 0
    printed = capsys.readouterr().out
    assert "Unmapped codes" in printed and "Water break" in printed and "5 events written" in printed
    items = evaluation.load_csv(out, video_file="g1.mp4")
    assert len(items) == 5 and items[0].type == "kickout" and items[0].attributes["length"] == "long"
    # --strict fails the run when anything didn't map.
    assert tags_cli(["convert", str(FIX / "sportscode_sample.xml"), "--mapping", str(FIX / "provider_mapping.yaml"),
                     "-o", str(out), "--strict"]) == 1


def test_cli_inspect_writes_starter(tmp_path, capsys):
    starter = tmp_path / "starter.yaml"
    assert tags_cli(["inspect", str(FIX / "sportscode_sample.xml"), "--starter", str(starter)]) == 0
    assert "Label group 'Result'" in capsys.readouterr().out
    load_mapping_text = starter.read_text()
    assert load_mapping_text.startswith("sport: gaa_football")
    assert tags_cli(["inspect", str(FIX / "missing.xml")]) == 2
