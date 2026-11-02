"""Password hashing, login sessions and the `current_user` dependency.

Sessions are random tokens sent as an HttpOnly cookie (so <video> and download links work)
or as `Authorization: Bearer <token>` for scripts. Only the token's SHA-256 is stored.
"""
import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import config
from .db import get_db
from .models import AuthSession, User

COOKIE = "ps_session"
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p))
    except ValueError:
        return False
    return hmac.compare_digest(got, base64.b64decode(digest))


# Checked when the email is unknown, so a login takes the same time whether or not the user exists.
_DUMMY_HASH = hash_password(secrets.token_hex(8))


def authenticate(db: Session, email: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None:
        verify_password(password, _DUMMY_HASH)
        return None
    return user if verify_password(password, user.password_hash) else None


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(db: Session, user: User, response: Response) -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=config.SESSION_DAYS)
    db.add(AuthSession(token_hash=_token_hash(token), user_id=user.id, expires_at=expires))
    db.commit()
    response.set_cookie(
        COOKIE, token, max_age=int(config.SESSION_DAYS * 86400), httponly=True,
        samesite="lax", secure=config.SECURE_COOKIES, path="/",
    )
    return token


def _request_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return request.cookies.get(COOKIE)


def end_session(db: Session, request: Request, response: Response) -> None:
    if token := _request_token(request):
        db.execute(delete(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
        db.commit()
    response.delete_cookie(COOKIE, path="/")


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = _request_token(request)
    if token:
        sess = db.scalar(select(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
        # sqlite drops tzinfo on read; treat stored times as UTC.
        if sess and sess.user and sess.expires_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc):
            return sess.user
    raise HTTPException(401, "Not signed in")


def admin_user(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Only club admins can do this")
    return user


CurrentUser = Annotated[User, Depends(current_user)]
AdminUser = Annotated[User, Depends(admin_user)]


# ---------- sign-in rate limiting ----------

def client_ip(request: Request) -> str:
    return (request.client.host if request.client else "") or "unknown"


def check_login_allowed(db: Session, email: str, ip: str) -> None:
    """429 while this email or IP has too many recent failures."""
    from sqlalchemy import func

    from .models import LoginFailure
    since = datetime.now(timezone.utc) - timedelta(seconds=config.LOGIN_WINDOW_S)
    db.execute(delete(LoginFailure).where(LoginFailure.at < since))  # forget old failures
    by_email = db.scalar(select(func.count()).select_from(LoginFailure).where(LoginFailure.email == email, LoginFailure.at >= since))
    by_ip = db.scalar(select(func.count()).select_from(LoginFailure).where(LoginFailure.ip == ip, LoginFailure.at >= since))
    db.commit()
    if by_email >= config.LOGIN_MAX_FAILURES_PER_EMAIL or by_ip >= config.LOGIN_MAX_FAILURES_PER_IP:
        minutes = max(1, config.LOGIN_WINDOW_S // 60)
        raise HTTPException(429, f"Too many failed sign-ins; try again in {minutes} minutes or reset your password",
                            headers={"Retry-After": str(config.LOGIN_WINDOW_S)})


def record_login_failure(db: Session, email: str, ip: str) -> None:
    from .models import LoginFailure
    db.add(LoginFailure(email=email, ip=ip))
    db.commit()


def clear_login_failures(db: Session, email: str) -> None:
    from .models import LoginFailure
    db.execute(delete(LoginFailure).where(LoginFailure.email == email))
    db.commit()


# ---------- single-use emailed tokens (invites, password resets) ----------

def issue_token(db: Session, kind: str, email: str, ttl: timedelta, **fields) -> str:
    from .models import AuthToken
    token = secrets.token_urlsafe(32)
    db.add(AuthToken(kind=kind, token_hash=_token_hash(token), email=email,
                     expires_at=datetime.now(timezone.utc) + ttl, **fields))
    db.commit()
    return token


def find_token(db: Session, kind: str, token: str):
    """The unused, unexpired token of this kind, or None."""
    from .models import AuthToken
    row = db.scalar(select(AuthToken).where(AuthToken.token_hash == _token_hash(token), AuthToken.kind == kind))
    if row is None or row.used_at is not None or row.expires_at.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc):
        return None
    return row


def end_other_sessions(db: Session, user: User, request: Request | None = None) -> None:
    """Sign the user out everywhere (except this request's session, if given)."""
    keep = _token_hash(t) if request is not None and (t := _request_token(request)) else None
    q = delete(AuthSession).where(AuthSession.user_id == user.id)
    if keep:
        q = q.where(AuthSession.token_hash != keep)
    db.execute(q)
    db.commit()
