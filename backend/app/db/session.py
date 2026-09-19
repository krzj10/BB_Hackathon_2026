"""SQLite connection handling and transaction management (A01).

Design rules:

- Persistence only. No Google, inference, STT or HTTP work may ever occur
  inside a transaction opened here; repositories keep transactions short.
- Timestamps are stored in one canonical representation: UTC ISO-8601 with an
  explicit ``+00:00`` offset. Naive datetimes are rejected on the way in, and
  because every stored value shares this fixed-width form, SQLite text
  comparison of timestamps is chronologically correct (expiry predicates rely
  on this). Host-local time is never used.
- Foreign-key enforcement is enabled per connection (SQLite defaults it off).
- ``BEGIN IMMEDIATE`` serializes writers so conditional UPDATEs (atomic claim)
  are race-free across connections; the busy timeout absorbs lock contention.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

DEFAULT_DB_URL = "sqlite:///./eva.db"
_BUSY_TIMEOUT_SECONDS = 10.0


class UnsupportedDatabaseUrl(ValueError):
    """Raised for database URLs this SQLite-only layer cannot serve."""


def sqlite_path(db_url: str) -> str:
    """Extract the file path from an ``sqlite:///`` URL (env contract EVA_DB_URL).

    Relative paths resolve against the process working directory; no
    developer-machine paths are hardcoded anywhere.
    """
    prefix = "sqlite:///"
    if not db_url.startswith(prefix) or not db_url[len(prefix):]:
        raise UnsupportedDatabaseUrl(
            f"only sqlite:/// database URLs are supported, got {db_url!r}"
        )
    return db_url[len(prefix):]


def to_db(dt: datetime) -> str:
    """Canonicalize an aware datetime to UTC ISO-8601 for storage."""
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ValueError("naive datetimes are rejected; contracts require aware values")
    return dt.astimezone(timezone.utc).isoformat()


def from_db(value: str) -> datetime:
    """Parse a stored timestamp; anything naive means a corrupted store."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise ValueError(f"stored timestamp is not timezone-aware: {value!r}")
    return parsed


class _Connection(sqlite3.Connection):
    """Context-managed connection: commit on success, rollback on failure,
    always close. Safe to use for plain reads (no active transaction)."""

    def __enter__(self) -> "_Connection":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        if self.in_transaction:
            if exc_type is None:
                self.execute("COMMIT")
            else:
                self.execute("ROLLBACK")
        self.close()
        return False


class Database:
    """Owns one SQLite file location; hands out connections and transactions."""

    def __init__(self, db_url: str = DEFAULT_DB_URL) -> None:
        self.path = sqlite_path(db_url)

    def connect(self) -> _Connection:
        conn = sqlite3.connect(
            self.path,
            timeout=_BUSY_TIMEOUT_SECONDS,
            isolation_level=None,  # explicit transaction control only
            factory=_Connection,
        )
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Short write transaction: BEGIN IMMEDIATE, COMMIT on clean exit,
        ROLLBACK on any exception (never a partial commit)."""
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
        except BaseException:
            conn.close()
            raise
        try:
            yield conn
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            finally:
                conn.close()
            raise
        else:
            try:
                conn.execute("COMMIT")
            finally:
                conn.close()
