"""Apply backend/sql/schema.sql to the Supabase Postgres database.

Usage:  backend\\.venv\\Scripts\\python.exe -m backend.scripts.apply_schema
Needs SUPABASE_DB_URL in .env (Supabase → Project Settings → Database →
Connection string, URI form, with the database password filled in).
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg

from backend.config import BACKEND_DIR, settings

SCHEMA = BACKEND_DIR / "sql" / "schema.sql"


def main() -> int:
    if not settings.supabase_db_url:
        print("SUPABASE_DB_URL is not set in .env — cannot run DDL through PostgREST.")
        print(f"Either set it, or paste {SCHEMA} into the Supabase SQL editor.")
        return 2
    sql = SCHEMA.read_text()
    with psycopg.connect(settings.supabase_db_url, autocommit=True) as conn:
        conn.execute(sql)
        with conn.cursor() as cur:
            cur.execute(
                "select table_name from information_schema.tables "
                "where table_schema='public' order by table_name"
            )
            tables = [r[0] for r in cur.fetchall()]
            cur.execute("select id, name, ST_AsText(geom) from regions order by id")
            regions = cur.fetchall()
    print("tables:", tables)
    print("regions:", regions)
    return 0


if __name__ == "__main__":
    sys.exit(main())
