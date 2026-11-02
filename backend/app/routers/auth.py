from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import auth, config, mailer, schemas
from ..auth import AdminUser, CurrentUser
from ..db import get_db
from ..models import AuthToken, Club, User

router = APIRouter()


def _email_free(db: Session, email: str) -> None:
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(409, "An account with that email already exists")


@router.post("/register", response_model=schemas.Me, status_code=201)
def register(body: schemas.Register, response: Response, db: Session = Depends(get_db)):
    """Create a new club and its first user (an admin), and sign them in."""
    if not config.ALLOW_SIGNUP:
        raise HTTPException(403, "Sign-up is closed; ask your club admin for an account")
    email = auth.normalize_email(body.email)
    _email_free(db, email)
    club = Club(name=body.club_name.strip())
    user = User(club=club, email=email, password_hash=auth.hash_password(body.password), role="admin")
    db.add_all([club, user])
    db.commit()
    auth.start_session(db, user, response)
    return user


@router.post("/login", response_model=schemas.Me)
def login(body: schemas.Login, request: Request, response: Response, db: Session = Depends(get_db)):
    email, ip = auth.normalize_email(body.email), auth.client_ip(request)
    auth.check_login_allowed(db, email, ip)
    user = auth.authenticate(db, email, body.password)
    if user is None:
        auth.record_login_failure(db, email, ip)
        raise HTTPException(401, "Wrong email or password")
    auth.clear_login_failures(db, email)
    auth.start_session(db, user, response)
    return user


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    auth.end_session(db, request, response)


@router.get("/me", response_model=schemas.Me)
def me(user: CurrentUser):
    return user


@router.get("/users", response_model=list[schemas.UserOut])
def list_users(user: CurrentUser, db: Session = Depends(get_db)):
    return db.scalars(select(User).where(User.club_id == user.club_id).order_by(User.email)).all()


@router.post("/users", response_model=schemas.UserOut, status_code=201)
def add_user(body: schemas.NewUser, admin: AdminUser, db: Session = Depends(get_db)):
    """Club admins add coaches to their own club."""
    email = auth.normalize_email(body.email)
    _email_free(db, email)
    user = User(club_id=admin.club_id, email=email, password_hash=auth.hash_password(body.password), role=body.role)
    db.add(user)
    db.commit()
    return user



@router.delete("/users/{user_id}", status_code=204)
def remove_user(user_id: int, admin: AdminUser, db: Session = Depends(get_db)):
    """Admins remove a member of their own club (not themselves: another admin must do that)."""
    user = db.get(User, user_id)
    if user is None or user.club_id != admin.club_id:
        raise HTTPException(404, "User not found")
    if user.id == admin.id:
        raise HTTPException(409, "You can't remove yourself")
    db.delete(user)
    db.commit()


# ---------- invites ----------

INVITE_TTL = timedelta(days=7)
RESET_TTL = timedelta(hours=1)


@router.post("/invites", status_code=201)
def invite(body: schemas.Invite, admin: AdminUser, db: Session = Depends(get_db)) -> dict:
    """Invite someone to the admin's club. They get an emailed link to choose a password. The link
    is also returned, for when email isn't set up yet."""
    email = auth.normalize_email(body.email)
    _email_free(db, email)
    token = auth.issue_token(db, "invite", email, INVITE_TTL, club_id=admin.club_id, role=body.role, created_by=admin.id)
    link = f"{config.PUBLIC_URL}/#invite={token}"
    mailer.send(email, f"You're invited to {admin.club.name} on PitchSight",
                f"{admin.email} invited you to join {admin.club.name} on PitchSight.\n\n"
                f"Choose a password here (the link works once, for 7 days):\n{link}\n")
    return {"email": email, "role": body.role, "link": link, "expires_days": INVITE_TTL.days}


@router.get("/invites/{token}")
def invite_info(token: str, db: Session = Depends(get_db)) -> dict:
    row = auth.find_token(db, "invite", token)
    if row is None:
        raise HTTPException(404, "This invite link is invalid, used or expired")
    return {"email": row.email, "club": db.get(Club, row.club_id).name, "role": row.role}


@router.post("/invites/accept", response_model=schemas.Me, status_code=201)
def accept_invite(body: schemas.AcceptInvite, response: Response, db: Session = Depends(get_db)):
    row = auth.find_token(db, "invite", body.token)
    if row is None:
        raise HTTPException(404, "This invite link is invalid, used or expired")
    _email_free(db, row.email)
    user = User(club_id=row.club_id, email=row.email, password_hash=auth.hash_password(body.password), role=row.role)
    db.add(user)
    row.used_at = datetime.now(timezone.utc)
    db.commit()
    auth.start_session(db, user, response)
    return user


# ---------- passwords ----------

@router.post("/password-reset", status_code=202)
def request_reset(body: schemas.ResetRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    """Email a reset link. Same answer whether or not the account exists (no account probing)."""
    from sqlalchemy import func
    email = auth.normalize_email(body.email)
    user = db.scalar(select(User).where(User.email == email))
    recent = db.scalar(select(func.count()).select_from(AuthToken).where(
        AuthToken.kind == "reset", AuthToken.email == email,
        AuthToken.expires_at > datetime.now(timezone.utc) + RESET_TTL - timedelta(hours=1)))
    if user is not None and recent < 3:  # at most 3 reset emails an hour per address
        token = auth.issue_token(db, "reset", email, RESET_TTL)
        mailer.send(email, "Reset your PitchSight password",
                    f"Someone (hopefully you) asked to reset the password for {email}.\n\n"
                    f"Choose a new one here (the link works once, for 1 hour):\n{config.PUBLIC_URL}/#reset={token}\n\n"
                    "If it wasn't you, ignore this email: your password hasn't changed.\n")
    return {"detail": "If that email has an account, a reset link is on its way"}


@router.post("/password-reset/confirm", response_model=schemas.Me)
def confirm_reset(body: schemas.ResetConfirm, response: Response, db: Session = Depends(get_db)):
    row = auth.find_token(db, "reset", body.token)
    user = row and db.scalar(select(User).where(User.email == row.email))
    if not user:
        raise HTTPException(404, "This reset link is invalid, used or expired")
    user.password_hash = auth.hash_password(body.password)
    row.used_at = datetime.now(timezone.utc)
    db.commit()
    auth.end_other_sessions(db, user)       # sign out everywhere, then in here
    auth.clear_login_failures(db, user.email)
    auth.start_session(db, user, response)
    return user


@router.post("/password", status_code=204)
def change_password(body: schemas.ChangePassword, request: Request, user: CurrentUser, db: Session = Depends(get_db)):
    if not auth.verify_password(body.current_password, user.password_hash):
        raise HTTPException(403, "Current password is wrong")
    user.password_hash = auth.hash_password(body.new_password)
    db.commit()
    auth.end_other_sessions(db, user, request)  # other devices are signed out; this one stays in
