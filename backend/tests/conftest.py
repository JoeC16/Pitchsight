import os
import tempfile

# Must be set before the app is imported: config reads the environment at import time.
os.environ["PITCHSIGHT_DATA_DIR"] = tempfile.mkdtemp(prefix="pitchsight-test-")

# Optional: run the suite against another database (e.g. Postgres in CI). It is wiped first, so
# only databases whose name contains "test" are accepted.
if test_db := os.environ.get("PITCHSIGHT_TEST_DB_URL"):
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    if "test" not in (make_url(test_db).database or ""):
        raise RuntimeError("PITCHSIGHT_TEST_DB_URL must name a database containing 'test' (it gets wiped)")
    with create_engine(test_db).begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    os.environ["PITCHSIGHT_DB_URL"] = test_db

import itertools

import pytest
from fastapi.testclient import TestClient

from app.main import app

_n = itertools.count()


def register(client: TestClient, club: str) -> dict:
    r = client.post("/api/auth/register",
                    json={"club_name": club, "email": f"coach{next(_n)}@example.com", "password": "correct horse"})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture(scope="session")
def client():
    """Signed in as the admin of 'Club A' (TestClient keeps the session cookie)."""
    with TestClient(app) as c:  # context manager runs the lifespan (runs migrations)
        c.me = register(c, "Club A")
        yield c


@pytest.fixture()
def other_club(client):
    """A second, separate club's signed-in client."""
    with TestClient(app) as c:
        c.me = register(c, "Club B")
        yield c


@pytest.fixture()
def anon(client):
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def match(client):
    r = client.post(
        "/api/matches",
        json={"title": "Test match", "sport": "gaa_football", "home_team": "Home GAC", "away_team": "Away GAC"},
    )
    assert r.status_code == 201
    return r.json()
