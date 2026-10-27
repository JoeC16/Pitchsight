import pytest

from app import config
from tests.test_api import fake_probe, upload  # noqa: F401 - fixture reuse


def test_register_login_logout(anon):
    creds = {"email": "Manager@Example.com", "password": "s3cret-pass"}
    r = anon.post("/api/auth/register", json={**creds, "club_name": "Ballyboden"})
    assert r.status_code == 201
    me = r.json()
    assert me["email"] == "manager@example.com" and me["role"] == "admin" and me["club"]["name"] == "Ballyboden"
    assert anon.get("/api/auth/me").json()["id"] == me["id"]

    assert anon.post("/api/auth/logout").status_code == 204
    assert anon.get("/api/auth/me").status_code == 401

    assert anon.post("/api/auth/login", json={**creds, "password": "wrong"}).status_code == 401
    assert anon.post("/api/auth/login", json={**creds, "email": "nobody@example.com"}).status_code == 401
    assert anon.post("/api/auth/login", json={**creds, "email": " MANAGER@example.com "}).status_code == 200
    assert anon.get("/api/auth/me").status_code == 200


def test_register_validation(anon, client):
    base = {"club_name": "X", "email": "a@example.com", "password": "longenough"}
    assert anon.post("/api/auth/register", json={**base, "password": "short"}).status_code == 422
    assert anon.post("/api/auth/register", json={**base, "email": "not-an-email"}).status_code == 422
    assert anon.post("/api/auth/register", json={**base, "email": client.me["email"]}).status_code == 409


def test_signup_can_be_closed(anon, monkeypatch):
    monkeypatch.setattr(config, "ALLOW_SIGNUP", False)
    r = anon.post("/api/auth/register", json={"club_name": "X", "email": "closed@example.com", "password": "longenough"})
    assert r.status_code == 403


def test_bearer_token_works_and_password_not_stored(anon):
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import AuthSession, User

    anon.post("/api/auth/register", json={"club_name": "Tok", "email": "tok@example.com", "password": "longenough"})
    token = anon.cookies.get("ps_session")
    anon.cookies.clear()
    assert anon.get("/api/auth/me").status_code == 401
    assert anon.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "tok@example.com"))
        assert "longenough" not in user.password_hash and user.password_hash.startswith("scrypt$")
        assert db.scalar(select(AuthSession).where(AuthSession.token_hash == token)) is None  # only the hash


def test_expired_session_rejected(anon):
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import update

    from app.db import SessionLocal
    from app.models import AuthSession

    me = anon.post("/api/auth/register", json={"club_name": "Exp", "email": "exp@example.com", "password": "longenough"}).json()
    with SessionLocal() as db:
        db.execute(update(AuthSession).where(AuthSession.user_id == me["id"])
                   .values(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
        db.commit()
    assert anon.get("/api/auth/me").status_code == 401


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/matches"), ("POST", "/api/matches"), ("GET", "/api/matches/1"),
    ("DELETE", "/api/matches/1"), ("PUT", "/api/matches/1/video"), ("GET", "/api/matches/1/video"),
    ("POST", "/api/matches/1/reprocess"), ("GET", "/api/matches/1/events"), ("POST", "/api/matches/1/events"),
    ("GET", "/api/matches/1/summary"), ("GET", "/api/events/1"), ("PATCH", "/api/events/1"),
    ("DELETE", "/api/events/1"), ("GET", "/api/events/1/clip"), ("GET", "/api/auth/users"),
])
def test_everything_but_sports_requires_login(anon, method, path):
    assert anon.request(method, path).status_code == 401


def test_clubs_cannot_see_each_others_data(client, other_club, match, fake_probe):  # noqa: F811
    upload(client, match["id"])
    event = client.get(f"/api/matches/{match['id']}/events").json()[0]
    m, e = match["id"], event["id"]

    assert m not in {x["id"] for x in other_club.get("/api/matches").json()}
    # Every per-match and per-event route is a 404 for another club (no hint the id exists).
    for method, path, body in [
        ("GET", f"/api/matches/{m}", None), ("GET", f"/api/matches/{m}/events", None),
        ("GET", f"/api/matches/{m}/summary", None), ("GET", f"/api/matches/{m}/video", None),
        ("POST", f"/api/matches/{m}/reprocess", None), ("PUT", f"/api/matches/{m}/video", None),
        ("POST", f"/api/matches/{m}/events", {"type": "free", "t_start": 1, "t_end": 2}),
        ("DELETE", f"/api/matches/{m}", None), ("GET", f"/api/events/{e}", None),
        ("PATCH", f"/api/events/{e}", {"team": "home"}), ("GET", f"/api/events/{e}/clip", None),
        ("DELETE", f"/api/events/{e}", None),
    ]:
        r = other_club.request(method, path, json=body)
        assert r.status_code == 404, f"{method} {path} -> {r.status_code}"

    # ...and nothing was changed by those attempts.
    assert client.get(f"/api/matches/{m}").status_code == 200
    assert client.get(f"/api/events/{e}").json() == event


def test_admin_adds_member_to_own_club(client, other_club):
    r = client.post("/api/auth/users", json={"email": "assistant@example.com", "password": "longenough"})
    assert r.status_code == 201 and r.json()["role"] == "member"
    emails = {u["email"] for u in client.get("/api/auth/users").json()}
    assert "assistant@example.com" in emails and other_club.me["email"] not in emails

    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as member:
        me = member.post("/api/auth/login", json={"email": "assistant@example.com", "password": "longenough"}).json()
        assert me["club"]["id"] == client.me["club"]["id"] and me["role"] == "member"
        # Members share the club's matches...
        mid = client.post("/api/matches", json={"title": "Shared", "sport": "soccer", "home_team": "a", "away_team": "b"}).json()["id"]
        assert member.get(f"/api/matches/{mid}").status_code == 200
        # ...but can't add users.
        assert member.post("/api/auth/users", json={"email": "x@example.com", "password": "longenough"}).status_code == 403


def test_cli_creates_user(tmp_path):
    from app.cli import create_user

    user = create_user("cli@example.com", "longenough", "CLI Club", None, "admin")
    assert user.id and user.role == "admin"
    with pytest.raises(SystemExit):
        create_user("cli@example.com", "longenough", "CLI Club", None, "admin")
