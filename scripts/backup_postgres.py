"""
scripts/backup_postgres.py — Standalone PostgreSQL Backup and Restore helper script.
"""
from __future__ import annotations

import argparse
import datetime
import os
import subprocess
import sys
from config import settings


def backup():
    if not settings.is_postgresql:
        print("Error: DATABASE_URL is not configured for PostgreSQL.", file=sys.stderr)
        sys.exit(1)
        
    now = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(os.path.dirname(__file__), "..", "backups")
    os.makedirs(backup_dir, exist_ok=True)
    filename = os.path.join(backup_dir, f"postgres_backup_{now}.dump")
    
    cmd = [
        "pg_dump",
        "--format=custom",
        f"--file={filename}",
        settings.database_url,
    ]
    print(f"Creating backup at: {filename}")
    try:
        subprocess.run(cmd, check=True)
        print("Backup created successfully.")
    except Exception as e:
        print(f"Backup failed: {e}", file=sys.stderr)
        sys.exit(1)


def restore(dump_file: str):
    if not settings.is_postgresql:
        print("Error: DATABASE_URL is not configured for PostgreSQL.", file=sys.stderr)
        sys.exit(1)
        
    cmd = [
        "pg_restore",
        "--clean",
        "--if-exists",
        f"--dbname={settings.database_url}",
        dump_file,
    ]
    print(f"Restoring database from: {dump_file}")
    try:
        subprocess.run(cmd, check=True)
        print("Restore completed successfully.")
    except Exception as e:
        print(f"Restore failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PostgreSQL Backup & Restore script")
    parser.add_argument("--backup", action="store_true", help="Create a timestamped custom dump backup")
    parser.add_argument("--restore", type=str, help="Restore from custom dump file")
    
    args = parser.parse_args()
    if args.backup:
        backup()
    elif args.restore:
        restore(args.restore)
    else:
        parser.print_help()
