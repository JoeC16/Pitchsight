from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import auth, config, schemas
from ..auth import AdminUser, CurrentUser
from ..db import get_db
from ..models import Club, User

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
def login(body: schemas.Login, response: Response, db: Session = Depends(get_db)):
    user = auth.authenticate(db, body.email, body.password)
    if user is None:
        raise HTTPException(401, "Wrong email or password")
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
