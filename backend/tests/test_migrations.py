"""Migrations run in a subprocess so each test gets its own database (config is read at import)."""
import os
import subprocess
import sys
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from app import models  # noqa: F401
from app.db import Base

BACKEND = Path(__file__).resolve().parents[1]


def run(data_dir: Path, code: str) -> None:
    env = {**os.environ, "PITCHSIGHT_DATA_DIR": str(data_dir)}
    env.pop("PITCHSIGHT_DB_URL", None)
    subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env, check=True)


def engine_for(data_dir: Path):
    return create_engine(f"sqlite:///{data_dir / 'pitchsight.db'}")


def test_upgrade_from_empty_matches_models(tmp_path):
    run(tmp_path, "from app.migrate import upgrade_db; upgrade_db()")
    with engine_for(tmp_path).connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], f"models and migrations disagree - autogenerate a revision: {diff}"


def test_downgrade_to_base_and_back(tmp_path):
    run(tmp_path, "from app.migrate import upgrade_db, alembic_config; from alembic import command; "
                  "upgrade_db(); command.downgrade(alembic_config(), 'base'); command.upgrade(alembic_config(), 'head')")
    assert "matches" in inspect(engine_for(tmp_path)).get_table_names()


def test_pre_alembic_database_is_adopted_and_keeps_data(tmp_path):
    # Simulate a database built by the old create_all() at the baseline schema.
    run(tmp_path, "from app.migrate import alembic_config; from alembic import command; "
                  "command.upgrade(alembic_config(), '0001')")
    eng = engine_for(tmp_path)
    with eng.begin() as conn:
        conn.execute(text("DROP TABLE alembic_version"))
        conn.execute(text("INSERT INTO matches (title, sport, home_team, away_team, status, progress, created_at) "
                          "VALUES ('Old', 'gaa_football', 'A', 'B', 'ready', 1.0, '2026-01-01')"))
    run(tmp_path, "from app.migrate import upgrade_db; upgrade_db()")
    with eng.connect() as conn:
        assert conn.execute(text("SELECT title FROM matches")).scalar() == "Old"
        # Pre-auth matches are moved into a "Default club".
        assert conn.execute(text("SELECT c.name FROM matches m JOIN clubs c ON c.id = m.club_id")).scalar() == "Default club"
        assert conn.execute(text("SELECT count(*) FROM alembic_version")).scalar() == 1
