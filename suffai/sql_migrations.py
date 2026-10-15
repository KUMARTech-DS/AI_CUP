"""Helpers for applying db/migrations/*.sql files (shared by API and worker)."""

from __future__ import annotations

from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "db" / "migrations"


def split_sql_statements(text: str) -> list[str]:
    """Split a migration on ';' after dropping full-line '--' comments."""
    body = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("--")
    )
    return [s.strip() for s in body.split(";") if s.strip()]


def migration_statements(filename: str) -> list[str]:
    path = MIGRATIONS_DIR / filename
    if not path.exists():
        return []
    return split_sql_statements(path.read_text(encoding="utf-8"))
