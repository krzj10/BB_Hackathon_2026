"""A01 durable SQLite state for EVA (Stream A - Core Platform).

Persistence-only boundary: repositories never perform network work, and all
transactions are short and local. External effects (Google, inference, STT)
belong to later tasks and must happen outside these transactions.
"""

from app.db.schema import SCHEMA_VERSION, SchemaVersionError, init_schema
from app.db.session import Database, UnsupportedDatabaseUrl

__all__ = [
    "Database",
    "SchemaVersionError",
    "SCHEMA_VERSION",
    "UnsupportedDatabaseUrl",
    "init_schema",
]
