"""Optional maintenance; request-time enforcement does not depend on it."""
from sqlalchemy import update
from app.database.models.moderation import UserRestriction
from app.database.session import SessionLocal
from app.database.types import utc_now


def main():
    now = utc_now()
    with SessionLocal.begin() as db:
        result = db.execute(update(UserRestriction).where(UserRestriction.status == "ACTIVE", UserRestriction.ends_at.is_not(None), UserRestriction.ends_at <= now).values(status="EXPIRED", updated_at=now))
    print(f"Expired restrictions updated: {result.rowcount}")


if __name__ == "__main__": main()
