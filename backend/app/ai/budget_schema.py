"""Expand a legacy ledger constraint without resetting reservations or editing schemas."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def upgrade_ledger(path: Path) -> int:
    """Only the recognized legacy CHECK changes; every ledger row and FK must survive."""
    with sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=30) as connection:
        # SQLite requires this connection-local setting before rebuilding a referenced table.
        # Foreign keys are checked in the same transaction before commit and re-enabled below.
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='api_call_ledger'"
            ).fetchone()
            if row is None:
                raise RuntimeError("The existing API ledger is required")
            ddl = row[0]
            previous = connection.execute(
                "SELECT * FROM api_call_ledger ORDER BY ordinal"
            ).fetchall()
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("Existing foreign key violations prevent ledger maintenance")
            if "ordinal >= 1" in ddl and "ordinal BETWEEN" not in ddl:
                connection.commit()
                return len(previous)
            if ddl.count("ordinal BETWEEN 1 AND 100") != 1:
                raise RuntimeError("Unrecognized ledger constraint")
            objects = connection.execute(
                "SELECT type, sql FROM sqlite_master WHERE tbl_name='api_call_ledger' "
                "AND type IN ('index','trigger') AND sql IS NOT NULL"
            ).fetchall()
            replacement = ddl.replace("ordinal BETWEEN 1 AND 100", "ordinal >= 1")
            replacement = replacement.replace("api_call_ledger", "api_call_ledger_expanded", 1)
            connection.execute(replacement)
            connection.execute(
                "INSERT INTO api_call_ledger_expanded SELECT * FROM api_call_ledger"
            )
            connection.execute("DROP TABLE api_call_ledger")
            connection.execute("ALTER TABLE api_call_ledger_expanded RENAME TO api_call_ledger")
            for _, sql in objects:
                connection.execute(sql)
            current = connection.execute(
                "SELECT * FROM api_call_ledger ORDER BY ordinal"
            ).fetchall()
            if current != previous or connection.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("Ledger reservations or foreign keys changed")
            connection.commit()
            return len(previous)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys=ON")
