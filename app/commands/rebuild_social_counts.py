"""Compare or repair cached Phase 11B post counters."""
import argparse
from app.database.session import session_scope
from app.services.social_counter_service import rebuild


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--apply", action="store_true"); args = parser.parse_args()
    with session_scope() as db: differences = rebuild(db, apply=args.apply)
    for post_id, counter, stored, expected in differences:
        print(f"post_id={post_id} counter={counter} stored={stored} expected={expected} difference={expected-stored}")
    print(f"Counter mismatches: {len(differences)} ({'applied' if args.apply else 'dry-run'})")
    raise SystemExit(1 if differences and not args.apply else 0)


if __name__ == "__main__": main()
