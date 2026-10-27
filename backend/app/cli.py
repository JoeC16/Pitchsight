"""Admin commands.

    python -m app.cli create-user EMAIL --club "St Mary's GAC" [--role admin|member]
    python -m app.cli create-user EMAIL --club-id 1

Prompts for the password. --club creates the club if no club has that name.
"""
import argparse
import getpass
import sys

from sqlalchemy import select

from . import auth
from .db import SessionLocal
from .migrate import upgrade_db
from .models import Club, User


def create_user(email: str, password: str, club_name: str | None, club_id: int | None, role: str) -> User:
    upgrade_db()
    with SessionLocal() as db:
        if club_id is not None:
            club = db.get(Club, club_id)
            if club is None:
                raise SystemExit(f"No club with id {club_id}")
        else:
            club = db.scalar(select(Club).where(Club.name == club_name)) or Club(name=club_name)
        email = auth.normalize_email(email)
        if db.scalar(select(User.id).where(User.email == email)):
            raise SystemExit(f"{email} already exists")
        user = User(club=club, email=email, password_hash=auth.hash_password(password), role=role)
        db.add(user)
        db.commit()
        print(f"Created {role} {email} in club #{club.id} '{club.name}'")
        return user


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    cu = sub.add_parser("create-user")
    cu.add_argument("email")
    where = cu.add_mutually_exclusive_group(required=True)
    where.add_argument("--club")
    where.add_argument("--club-id", type=int)
    cu.add_argument("--role", choices=["admin", "member"], default="admin")
    args = parser.parse_args(argv)

    password = getpass.getpass("Password: ") if sys.stdin.isatty() else sys.stdin.readline().rstrip("\n")
    if len(password) < 8:
        raise SystemExit("Password must be at least 8 characters")
    create_user(args.email, password, args.club, args.club_id, args.role)


if __name__ == "__main__":
    main()
