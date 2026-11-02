import itertools
import re

import pytest
from fastapi.testclient import TestClient

from app import config, mailer
from app.main import app

_n = itertools.count()


def fresh(email=None, club="Sec Club"):
    """A signed-in client for a new club."""
    c = TestClient(app)
    email = email or f"sec{next(_n)}@security.example.com"
    assert c.post("/api/auth/register", json={"club_name": club, "email": email, "password": "orig-password"}).status_code == 201
    c.email = email
    return c


def last_link(kind: str) -> str:
    body = mailer.outbox[-1].get_content()
    return re.search(rf"#{kind}=([\w-]+)", body).group(1)


# ---------- CSRF ----------

def test_cookie_writes_need_the_csrf_header(client):
    c = fresh()
    del c.headers["X-PitchSight"]
    r = c.post("/api/matches", json={"title": "x", "sport": "soccer", "home_team": "a", "away_team": "b"})
    assert r.status_code == 403 and "CSRF" in r.json()["detail"]
    assert c.get("/api/matches").status_code == 200        # reads are fine
    c.headers["X-PitchSight"] = "1"
    assert c.post("/api/matches", json={"title": "x", "sport": "soccer", "home_team": "a", "away_team": "b"}).status_code == 201


def test_bearer_token_callers_dont_need_the_header(client):
    c = fresh()
    token = c.cookies.get("ps_session")
    script = TestClient(app)
    del script.headers["X-PitchSight"]
    r = script.post("/api/matches", headers={"Authorization": f"Bearer {token}"},
                    json={"title": "x", "sport": "soccer", "home_team": "a", "away_team": "b"})
    assert r.status_code == 201


# ---------- headers ----------

def test_security_headers(anon):
    r = anon.get("/")
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "unsafe-inline" not in csp
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
    assert anon.get("/api/health").headers["referrer-policy"] == "same-origin"


def test_csp_allows_the_bucket_origin(monkeypatch):
    from app.security import content_security_policy
    monkeypatch.setenv("PITCHSIGHT_S3_ENDPOINT", "https://abc.r2.cloudflarestorage.com")
    csp = content_security_policy()
    assert "media-src 'self' blob: https://abc.r2.cloudflarestorage.com" in csp
    assert "connect-src 'self' https://abc.r2.cloudflarestorage.com" in csp


# ---------- sign-in rate limiting ----------

def test_repeated_failures_lock_the_account_for_a_while(client, monkeypatch):
    c = fresh()
    anon = TestClient(app)
    for _ in range(config.LOGIN_MAX_FAILURES_PER_EMAIL):
        assert anon.post("/api/auth/login", json={"email": c.email, "password": "guess"}).status_code == 401
    r = anon.post("/api/auth/login", json={"email": c.email, "password": "orig-password"})
    assert r.status_code == 429 and r.headers["retry-after"] == str(config.LOGIN_WINDOW_S)  # even the right password
    # After the window, it works again (simulate time passing by shrinking the window).
    monkeypatch.setattr(config, "LOGIN_WINDOW_S", 0)
    assert anon.post("/api/auth/login", json={"email": c.email, "password": "orig-password"}).status_code == 200


def test_success_resets_the_failure_count(client):
    c = fresh()
    anon = TestClient(app)
    for _ in range(config.LOGIN_MAX_FAILURES_PER_EMAIL - 1):
        anon.post("/api/auth/login", json={"email": c.email, "password": "guess"})
    assert anon.post("/api/auth/login", json={"email": c.email, "password": "orig-password"}).status_code == 200
    for _ in range(config.LOGIN_MAX_FAILURES_PER_EMAIL - 1):
        assert anon.post("/api/auth/login", json={"email": c.email, "password": "guess"}).status_code == 401


def test_ip_limit(client, monkeypatch):
    monkeypatch.setattr(config, "LOGIN_MAX_FAILURES_PER_IP", 3)
    anon = TestClient(app)
    for i in range(3):  # different emails, same IP
        anon.post("/api/auth/login", json={"email": f"nobody{i}@iplimit.example.com", "password": "x"})
    assert anon.post("/api/auth/login", json={"email": "other@iplimit.example.com", "password": "x"}).status_code == 429


# ---------- invites ----------

def test_invite_flow(client):
    admin = fresh(club="Invite GAA")
    r = admin.post("/api/auth/invites", json={"email": "New.Coach@Example.com", "role": "member"})
    assert r.status_code == 201 and r.json()["email"] == "new.coach@example.com"
    token = last_link("invite")
    assert r.json()["link"].endswith(f"#invite={token}")
    assert "Invite GAA" in mailer.outbox[-1]["Subject"] and mailer.outbox[-1]["To"] == "new.coach@example.com"

    newbie = TestClient(app)
    assert newbie.get(f"/api/auth/invites/{token}").json() == {"email": "new.coach@example.com", "club": "Invite GAA", "role": "member"}
    me = newbie.post("/api/auth/invites/accept", json={"token": token, "password": "brand-new-pw"}).json()
    assert me["club"]["name"] == "Invite GAA" and me["role"] == "member"
    # Single use.
    assert TestClient(app).post("/api/auth/invites/accept", json={"token": token, "password": "brand-new-pw"}).status_code == 404
    assert newbie.post("/api/auth/invites", json={"email": "x@example.com"}).status_code == 403  # members can't invite


def test_expired_or_bogus_invites(client):
    from datetime import datetime, timedelta, timezone

    from app.db import SessionLocal
    from app.models import AuthToken
    admin = fresh()
    admin.post("/api/auth/invites", json={"email": "late@example.com"})
    token = last_link("invite")
    with SessionLocal() as db:
        db.query(AuthToken).filter(AuthToken.email == "late@example.com").update(
            {"expires_at": datetime.now(timezone.utc) - timedelta(minutes=1)})
        db.commit()
    assert TestClient(app).get(f"/api/auth/invites/{token}").status_code == 404
    assert TestClient(app).get("/api/auth/invites/not-a-real-token-at-all").status_code == 404


def test_only_hashes_of_tokens_are_stored(client):
    from app.db import SessionLocal
    from app.models import AuthToken
    fresh().post("/api/auth/invites", json={"email": "hash@example.com"})
    token = last_link("invite")
    with SessionLocal() as db:
        assert not db.query(AuthToken).filter(AuthToken.token_hash == token).count()


# ---------- password reset / change ----------

def test_password_reset_flow_signs_out_everywhere(client):
    c = fresh()
    anon = TestClient(app)
    r = anon.post("/api/auth/password-reset", json={"email": c.email.upper()})
    assert r.status_code == 202
    token = last_link("reset")
    assert anon.post("/api/auth/password-reset/confirm", json={"token": token, "password": "short"}).status_code == 422
    me = anon.post("/api/auth/password-reset/confirm", json={"token": token, "password": "after-reset-pw"})
    assert me.status_code == 200 and anon.get("/api/auth/me").status_code == 200   # signed in with the new one
    assert c.get("/api/auth/me").status_code == 401                                # old session ended
    assert TestClient(app).post("/api/auth/login", json={"email": c.email, "password": "orig-password"}).status_code == 401
    assert TestClient(app).post("/api/auth/password-reset/confirm", json={"token": token, "password": "again-pw-123"}).status_code == 404


def test_reset_does_not_reveal_whether_an_account_exists(client):
    before = len(mailer.outbox)
    a = TestClient(app).post("/api/auth/password-reset", json={"email": "ghost@nowhere.example.com"})
    assert a.status_code == 202 and len(mailer.outbox) == before   # same answer, nothing sent
    c = fresh()
    b = TestClient(app).post("/api/auth/password-reset", json={"email": c.email})
    assert b.json() == a.json()


def test_reset_emails_are_rate_limited(client):
    c = fresh()
    before = len(mailer.outbox)
    for _ in range(5):
        TestClient(app).post("/api/auth/password-reset", json={"email": c.email})
    assert len(mailer.outbox) - before == 3


def test_change_password_keeps_this_session_ends_others(client):
    c = fresh()
    other = TestClient(app)
    other.post("/api/auth/login", json={"email": c.email, "password": "orig-password"})
    assert c.post("/api/auth/password", json={"current_password": "wrong", "new_password": "new-password-1"}).status_code == 403
    assert c.post("/api/auth/password", json={"current_password": "orig-password", "new_password": "new-password-1"}).status_code == 204
    assert c.get("/api/auth/me").status_code == 200 and other.get("/api/auth/me").status_code == 401


# ---------- members, export, delete ----------

def test_removed_member_is_signed_out(client):
    admin = fresh()
    admin.post("/api/auth/invites", json={"email": "leaver@example.com"})
    leaver = TestClient(app)
    uid = leaver.post("/api/auth/invites/accept", json={"token": last_link("invite"), "password": "leaver-pass"}).json()["id"]
    assert admin.delete(f"/api/auth/users/{uid}").status_code == 204
    assert leaver.get("/api/auth/me").status_code == 401   # not a crash: their sessions went with them
    me = admin.get("/api/auth/me").json()["id"]
    assert admin.delete(f"/api/auth/users/{me}").status_code == 409


def test_other_clubs_members_cannot_be_removed(client, other_club):
    assert other_club.delete(f"/api/auth/users/{client.me['id']}").status_code == 404


def test_export_and_delete_club(client, tmp_path):
    c = fresh(club="Leaving FC")
    m = c.post("/api/matches", json={"title": "Final", "sport": "soccer", "home_team": "a", "away_team": "b"}).json()
    c.post(f"/api/matches/{m['id']}/events", json={"type": "shot", "team": "home", "t_start": 5, "t_end": 6})
    r = c.get("/api/club/export")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    data = r.json()
    assert data["club"]["name"] == "Leaving FC" and data["matches"][0]["events"][0]["type"] == "shot"
    assert "password" not in r.text and "scrypt" not in r.text

    assert c.request("DELETE", "/api/club", json={"confirm": "wrong name"}).status_code == 422
    assert c.request("DELETE", "/api/club", json={"confirm": "Leaving FC"}).status_code == 204
    assert c.get("/api/auth/me").status_code == 401
    assert TestClient(app).post("/api/auth/login", json={"email": c.email, "password": "orig-password"}).status_code == 401
    assert client.get(f"/api/matches/{m['id']}").status_code == 404
