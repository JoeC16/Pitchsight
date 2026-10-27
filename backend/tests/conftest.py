import os
import tempfile

# Must be set before the app is imported: config reads the environment at import time.
os.environ["PITCHSIGHT_DATA_DIR"] = tempfile.mkdtemp(prefix="pitchsight-test-")

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:  # context manager runs the lifespan (creates tables)
        yield c


@pytest.fixture()
def match(client):
    r = client.post(
        "/api/matches",
        json={"title": "Test match", "sport": "gaa_football", "home_team": "Home GAC", "away_team": "Away GAC"},
    )
    assert r.status_code == 201
    return r.json()
