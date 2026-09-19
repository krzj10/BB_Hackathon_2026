"""SQLite schema definition and version metadata (A01).

Versioning strategy (deliberately lightweight - no migration framework yet):

- ``meta(schema_version)`` records which schema the file contains.
- A fresh database is created deterministically and stamped with
  ``SCHEMA_VERSION``.
- Reopening a compatible database is a no-op (all DDL is IF NOT EXISTS).
- An incompatible version - or pre-existing tables without metadata - raises
  :class:`SchemaVersionError`; the file is never silently destroyed or
  recreated. Bumping ``SCHEMA_VERSION`` before A07 implies writing a real
  migration step at that point (documented decision).

Security-relevant invariants from the frozen A00 contracts are mirrored as DB
CHECK constraints (defense in depth): HIGH risk always requires approval and
never allows voice approval, and status/risk/enum domains are closed sets.
"""

from __future__ import annotations

from app.contracts import domain
from app.db.session import Database

SCHEMA_VERSION = 1


class SchemaVersionError(RuntimeError):
    """Existing database is incompatible; refusing to touch it."""


def _in(values: object) -> str:
    return "(" + ", ".join(f"'{v.value if hasattr(v, 'value') else v}'" for v in values) + ")"


_STATUS = _in(domain.ProposedActionStatus)
_RISK = _in(domain.ActionRisk)
_CHANNEL = _in(domain.ApprovalChannel)
_SOURCE_SYSTEM = _in(domain.SourceSystem)
_DECISION_STATUS = _in(domain.DecisionStatus)

DDL_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sessions (
        id         TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS proposed_actions (
        id                     TEXT PRIMARY KEY,
        session_id             TEXT NOT NULL REFERENCES sessions(id),
        request_id             TEXT NOT NULL,
        revision               INTEGER NOT NULL CHECK (revision >= 1),
        tool                   TEXT NOT NULL,
        arguments_json         TEXT NOT NULL,
        arguments_digest       TEXT NOT NULL,
        summary                TEXT NOT NULL,
        reason                 TEXT NOT NULL,
        impact                 TEXT NOT NULL,
        before_json            TEXT,
        after_json             TEXT,
        resource_version       TEXT,
        policy_version         TEXT NOT NULL,
        risk                   TEXT NOT NULL CHECK (risk IN {_RISK}),
        requires_approval      INTEGER NOT NULL CHECK (requires_approval IN (0, 1)),
        voice_approval_allowed INTEGER NOT NULL CHECK (voice_approval_allowed IN (0, 1)),
        created_at             TEXT NOT NULL,
        expires_at             TEXT NOT NULL,
        status                 TEXT NOT NULL CHECK (status IN {_STATUS}),
        CHECK (risk <> 'high' OR (requires_approval = 1 AND voice_approval_allowed = 0))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_actions_session ON proposed_actions(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_actions_status ON proposed_actions(status)",
    f"""
    CREATE TABLE IF NOT EXISTS approval_receipts (
        id               TEXT PRIMARY KEY,
        action_id        TEXT NOT NULL REFERENCES proposed_actions(id),
        revision         INTEGER NOT NULL CHECK (revision >= 1),
        arguments_digest TEXT NOT NULL,
        channel          TEXT NOT NULL CHECK (channel IN {_CHANNEL}),
        approved_at      TEXT NOT NULL,
        policy_version   TEXT NOT NULL,
        -- one receipt per action revision: a repeated approval is a duplicate,
        -- never a second authorization for the same mutation.
        UNIQUE (action_id, revision)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS execution_attempts (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        action_id  TEXT NOT NULL REFERENCES proposed_actions(id),
        revision   INTEGER NOT NULL CHECK (revision >= 1),
        started_at TEXT NOT NULL,
        finished_at TEXT,
        outcome    TEXT CHECK (outcome IS NULL OR outcome IN ('succeeded', 'failed', 'unknown')),
        detail     TEXT
    )
    """,
    # An action revision may have at most one attempt in flight: duplicate
    # execution is blocked at the storage boundary, independent of application
    # checks. (A plain UNIQUE would not work: SQLite treats NULL as distinct.)
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_one_active_attempt "
    "ON execution_attempts(action_id, revision) WHERE finished_at IS NULL",
    f"""
    CREATE TABLE IF NOT EXISTS attention_items (
        id           TEXT PRIMARY KEY,
        source       TEXT NOT NULL CHECK (source IN {_SOURCE_SYSTEM}),
        source_id    TEXT NOT NULL,
        received_at  TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        -- canonical dedup key for source-backed ingestion (A06)
        UNIQUE (source, source_id)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS decisions (
        id                 TEXT PRIMARY KEY,
        attention_item_id  TEXT NOT NULL UNIQUE REFERENCES attention_items(id),
        status             TEXT NOT NULL CHECK (status IN {_DECISION_STATUS}),
        proposed_action_id TEXT,
        payload_json       TEXT NOT NULL
        -- UNIQUE(attention_item_id): at most one Decision per Attention item.
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS focus_sessions (
        id          TEXT PRIMARY KEY,
        starts_at   TEXT NOT NULL,
        ends_at     TEXT NOT NULL,
        stopped_at  TEXT,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS focus_completion_summaries (
        focus_session_id TEXT PRIMARY KEY REFERENCES focus_sessions(id),
        ended_at         TEXT NOT NULL,
        payload_json     TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ingestion_cursors (
        source          TEXT PRIMARY KEY,
        high_water_mark TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS seen_sources (
        source        TEXT NOT NULL,
        source_id     TEXT NOT NULL,
        first_seen_at TEXT NOT NULL,
        PRIMARY KEY (source, source_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS event_outbox (
        seq            INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id       TEXT NOT NULL UNIQUE,
        session_id     TEXT NOT NULL,
        request_id     TEXT,
        occurred_at    TEXT NOT NULL,
        schema_version INTEGER NOT NULL DEFAULT 1,
        type           TEXT NOT NULL,
        payload_json   TEXT NOT NULL
        -- Storage boundary only (A01): delivery, replay and consumption are A07.
    )
    """,
    # ------------------------------------------------------------------ #
    # A04 guarded execution state (additive under schema version 1).
    # Runtime execution bookkeeping lives here - deliberately NOT in
    # ProposedAction.arguments, ApprovalReceipt or event_outbox.
    # ------------------------------------------------------------------ #
    """
    CREATE TABLE IF NOT EXISTS google_event_ids (
        action_id       TEXT NOT NULL REFERENCES proposed_actions(id),
        revision        INTEGER NOT NULL CHECK (revision >= 1),
        -- Client-generated Google-valid event id, reserved durably BEFORE the
        -- first create request so a lost response can be reconciled by GET.
        google_event_id TEXT NOT NULL UNIQUE,
        created_at      TEXT NOT NULL,
        PRIMARY KEY (action_id, revision)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS action_execution_results (
        action_id   TEXT PRIMARY KEY REFERENCES proposed_actions(id),
        -- Canonical ToolResult JSON for the last completed execution; lets
        -- GET /api/actions/{id} and duplicate requests replay the stored
        -- outcome without touching Google again.
        status      TEXT NOT NULL,
        result_json TEXT NOT NULL,
        updated_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS proposal_idempotency (
        session_id       TEXT NOT NULL,
        request_id       TEXT NOT NULL,
        arguments_digest TEXT NOT NULL,
        action_id        TEXT NOT NULL REFERENCES proposed_actions(id),
        -- Same (session, request) with the same canonical digest replays the
        -- existing action; a different digest is rejected at this boundary.
        PRIMARY KEY (session_id, request_id)
    )
    """,
)


def init_schema(database: Database) -> None:
    """Initialize a fresh database or reopen a compatible one.

    Never destroys data: incompatible versions and metadata-less databases
    with pre-existing tables raise :class:`SchemaVersionError` before any DDL
    runs.
    """
    with database.transaction() as conn:
        has_meta = (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'"
            ).fetchone()
            is not None
        )
        if has_meta:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                raise SchemaVersionError(
                    f"database {database.path!r} has a meta table without schema_version; "
                    "refusing to touch it"
                )
            found = int(row[0])
            if found != SCHEMA_VERSION:
                raise SchemaVersionError(
                    f"database {database.path!r} has schema version {found}, this build "
                    f"requires {SCHEMA_VERSION}; refusing to touch it"
                )
        else:
            other = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if other:
                raise SchemaVersionError(
                    f"database {database.path!r} contains unversioned tables "
                    f"{[r[0] for r in other]}; refusing to adopt or destroy it"
                )

        for statement in DDL_STATEMENTS:
            conn.execute(statement)
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO NOTHING",
            (str(SCHEMA_VERSION),),
        )
