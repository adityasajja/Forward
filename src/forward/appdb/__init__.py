"""Control-plane persistence: users, sessions, watchlists, audit.

Stores users, sessions, watchlists, and audit records. SQLite is the local
default, and an explicit SQLAlchemy URL can use a shared database.

See ``docs/DATA_MODEL.md`` section 1 for the normative DDL.
"""

from __future__ import annotations

from forward.appdb.engine import AppDatabase, get_app_db

__all__ = [
    "AppDatabase",
    "get_app_db",
]
