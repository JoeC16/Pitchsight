"""Bring the database schema up to date with Alembic. Called on app startup."""
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from .db import engine

BACKEND_DIR = Path(__file__).resolve().parents[1]
# The revision that matches the schema the pre-Alembic scaffold built with create_all().
BASELINE = "0001"


def alembic_config() -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.attributes["from_app"] = True
    return cfg


def upgrade_db() -> None:
    cfg = alembic_config()
    tables = set(inspect(engine).get_table_names())
    if "matches" in tables and "alembic_version" not in tables:
        # Database made by create_all() before migrations existed: adopt it at the baseline.
        command.stamp(cfg, BASELINE)
    command.upgrade(cfg, "head")
