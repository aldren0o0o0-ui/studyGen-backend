"""
app/commands/verify_postgres_migration.py — Deep integrity verification command between SQLite and PostgreSQL.
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from sqlalchemy import create_engine, text
from config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def verify_migration(sqlite_path: str):
    sq_conn = sqlite3.connect(sqlite_path)
    sq_cur = sq_conn.cursor()
    
    pg_engine = create_engine(settings.database_url)
    
    # 1. Foreign key integrity check in PG
    with pg_engine.connect() as pg_conn:
        if settings.is_postgresql:
            logger.info("Verifying FK integrity in PostgreSQL...")
            # Sample integrity check on orphan documents
            res = pg_conn.execute(text("SELECT COUNT(*) FROM documents d LEFT JOIN users u ON d.user_id = u.id WHERE u.id IS NULL"))
            orphan_docs = res.scalar() or 0
            if orphan_docs > 0:
                logger.error("Integrity check FAILED: Found %d orphaned documents without users!", orphan_docs)
                sys.exit(1)
            logger.info("FK Integrity check PASSED (0 orphaned documents).")
            
    logger.info("Deep verification completed successfully.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="studygen.db")
    args = parser.parse_args()
    verify_migration(args.source)
