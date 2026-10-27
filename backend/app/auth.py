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
        if sess and sess.expires_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc):
            return sess.user
    raise HTTPException(401, "Not signed in")


def admin_user(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Only club admins can do this")
    return user


CurrentUser = Annotated[User, Depends(current_user)]
AdminUser = Annotated[User, Depends(admin_user)]
