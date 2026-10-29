"""Deliberate initial/additional Admin bootstrap command."""
import argparse,getpass
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from uuid import uuid4
from app.database.models.auth import User
from app.database.session import SessionLocal
from app.database.types import utc_now
from app.schemas.auth import RegisterRequest
from app.services.auth_service import normalize_email
from app.services.community_profile_service import normalize_username
from app.services.password_service import hash_password
from config import settings
class BootstrapError(RuntimeError):pass
def create_admin(db,*,email,username,display_name,password):
    validated=RegisterRequest(email=email,password=password,display_name=display_name);normalized_email=normalize_email(validated.email);normalized_username=normalize_username(username,settings.community_username_min_length,settings.community_username_max_length)
    by_email=db.scalar(select(User).where(User.normalized_email==normalized_email));by_username=db.scalar(select(User).where(User.normalized_username==normalized_username));existing=by_email or by_username
    if existing:
        if existing.role=="ADMIN" and existing.normalized_email==normalized_email and existing.normalized_username==normalized_username:raise BootstrapError("Admin account already exists for this identity.")
        raise BootstrapError("Email or username belongs to an existing account; refusing silent promotion.")
    now=utc_now();row=User(id=str(uuid4()),email=validated.email.strip(),normalized_email=normalized_email,password_hash=hash_password(password,minimum=settings.password_min_length),display_name=validated.display_name,username=username.strip(),normalized_username=normalized_username,status="ACTIVE",role="ADMIN",password_changed_at=now,created_at=now,updated_at=now)
    try:db.add(row);db.commit();db.refresh(row);return row
    except IntegrityError as exc:db.rollback();raise BootstrapError("Email or username already exists.") from exc
def main():
    parser=argparse.ArgumentParser();parser.add_argument("--email");parser.add_argument("--username");parser.add_argument("--display-name");parser.add_argument("--yes",action="store_true");args=parser.parse_args();url=make_url(settings.database_url);print(f"Target database:\n{url.get_backend_name()} / {url.database}\nEnvironment: {settings.environment}")
    if not args.yes and input("Continue? [y/N] ").strip().lower()!="y":print("Cancelled.");return 1
    email=args.email or input("Email: ").strip();username=args.username or input("Username: ").strip();display=args.display_name or input("Display name: ").strip();password=getpass.getpass("Password: ");confirm=getpass.getpass("Confirm password: ")
    if password!=confirm:print("Passwords do not match.");return 1
    db=SessionLocal()
    try:row=create_admin(db,email=email,username=username,display_name=display,password=password);print(f"Admin created: {row.email} (@{row.username})");return 0
    except (BootstrapError,ValueError) as exc:db.rollback();print(f"Admin not created: {exc}");return 1
    finally:db.close()
if __name__=="__main__":raise SystemExit(main())
