import os
import tempfile

# Must be set before the app is imported: config reads the environment at import time.
os.environ["PITCHSIGHT_DATA_DIR"] = tempfile.mkdtemp(prefix="pitchsight-test-")

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
