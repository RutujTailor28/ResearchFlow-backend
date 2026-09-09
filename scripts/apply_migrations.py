#!/usr/bin/env python3
"""Apply SQL migrations in backend/migrations using DATABASE_URL."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import clear_settings_cache, get_settings  # noqa: E402


def main() -> int:
    clear_settings_cache()
    settings = get_settings()
    database_url = settings.database_url
    if not database_url:
        print(
            "DATABASE_URL is not set.\n"
            "Add your Supabase Postgres connection string to backend/.env, then re-run:\n"
            "  python scripts/apply_migrations.py\n\n"
            "Find it in Supabase → Project Settings → Database → Connection string (URI).\n"
            "Alternatively, paste migrations/001_create_runs.sql into the SQL Editor."
        )
        return 1

    try:
        import psycopg
    except ImportError:
        print("Install psycopg first: pip install 'psycopg[binary]'")
        return 1

    migrations_dir = BACKEND_ROOT / "migrations"
    files = sorted(migrations_dir.glob("*.sql"))
    if not files:
        print("No migration files found.")
        return 1

    print(f"Applying {len(files)} migration(s)...")
    with psycopg.connect(database_url) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS public.schema_migrations (
                filename TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
            );
            """
        )
        for path in files:
            exists = conn.execute(
                "SELECT 1 FROM public.schema_migrations WHERE filename = %s",
                (path.name,),
            ).fetchone()
            if exists:
                print(f"  skip {path.name} (already applied)")
                continue
            sql = path.read_text(encoding="utf-8")
            print(f"  apply {path.name}...")
            conn.execute(sql)
            conn.execute(
                "INSERT INTO public.schema_migrations (filename) VALUES (%s)",
                (path.name,),
            )
            conn.commit()
            print(f"  done {path.name}")

    print("Migrations complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
