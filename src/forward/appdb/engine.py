"""Engine, session factory and SQLite pragmas for the control-plane store.

The store is import-safe: no connection is opened until something asks for a
session, so importing :mod:`forward.appdb` in a unit test or a backtest costs
nothing.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import forward.appdb.schema_registry as _schema_registry  # noqa: F401
from forward.appdb.account_schema import users
from forward.appdb.metadata import app_metadata
from forward.config.settings import PROJECT_ROOT
from forward.core.time import utcnow


def _resolve_url(url: str) -> str:
    """Turn a relative SQLite path into an absolute one."""
    prefix = "sqlite:///"
    if url.startswith(prefix):
        raw = url[len(prefix) :]
        if raw and raw != ":memory:" and not Path(raw).is_absolute():
            path = (PROJECT_ROOT / raw).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            return f"{prefix}{path.as_posix()}"
    return url


class AppDatabase:
    """Thin wrapper around a SQLAlchemy engine + session factory."""

    def __init__(self, url: str | None = None, *, echo: bool = False) -> None:
        self._explicit_url = url
        self._echo = echo
        self._engine: Engine | None = None
        self._session_factory: sessionmaker[Session] | None = None
        self._prepared = False

    # ------------------------------------------------------------------ url
    @property
    def url(self) -> str:
        if self._explicit_url:
            return _resolve_url(self._explicit_url)
        from forward.config.settings import get_settings

        return _resolve_url(get_settings().app_db_url)

    @property
    def is_sqlite(self) -> bool:
        return self.url.startswith("sqlite")

    @property
    def sqlite_file(self) -> Path:
        """Return the configured SQLite database file used by local backups.

        Returns:
            Absolute path to the configured SQLite file.

        Raises:
            NotImplementedError: If the configured database is not a file-backed
                SQLite database.
        """
        url = make_url(self.url)
        if url.get_backend_name() != "sqlite":
            raise NotImplementedError(
                "Local ZIP backups support file-backed SQLite only; "
                f"configured database backend is {url.get_backend_name()}."
            )
        if not url.database or url.database == ":memory:":
            raise NotImplementedError(
                "Local ZIP backups require a file-backed SQLite database."
            )
        database = Path(url.database)
        if not database.is_absolute():
            database = (PROJECT_ROOT / database).resolve()
        return database

    # --------------------------------------------------------------- engine
    @property
    def engine(self) -> Engine:
        if self._engine is None:
            url = self.url
            kwargs: dict = {"echo": self._echo, "future": True}
            if url.startswith("sqlite"):
                # A file-backed SQLite connection pool hands the same connection
                # to different worker threads; FastAPI runs sync routes in a
                # threadpool, so check_same_thread must be off or requests fail
                # intermittently under any real concurrency.
                kwargs["connect_args"] = {"check_same_thread": False, "timeout": 15.0}
                if ":memory:" in url:
                    # An in-memory database lives and dies with its connection,
                    # so it must be a single shared one or every session sees an
                    # empty schema.
                    kwargs["poolclass"] = StaticPool
            else:
                kwargs["pool_pre_ping"] = True
                kwargs["pool_size"] = 5
                kwargs["max_overflow"] = 5
            self._engine = create_engine(url, **kwargs)
            if url.startswith("sqlite"):
                self._install_sqlite_pragmas(self._engine)
        return self._engine

    @staticmethod
    def _install_sqlite_pragmas(engine: Engine) -> None:
        @event.listens_for(engine, "connect")
        def _on_connect(dbapi_conn, _record) -> None:  # pragma: no cover - driver hook
            cursor = dbapi_conn.cursor()
            try:
                # OFF by default in SQLite, which silently disables every
                # ON DELETE CASCADE in the schema — deleting a user would leave
                # their watchlists behind.
                cursor.execute("PRAGMA foreign_keys=ON")
                # WAL lets a reader proceed during a write; without it the
                # dashboard's polling reads collide with audit writes.
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute("PRAGMA busy_timeout=15000")
                # Ultra-low latency memory mapping: map up to 256MB into RAM for zero-copy reads
                cursor.execute("PRAGMA mmap_size=268435456")
                cursor.execute("PRAGMA cache_size=-64000")  # 64MB page cache in RAM
                cursor.execute("PRAGMA temp_store=MEMORY")
            finally:
                cursor.close()
            # Hand transaction control to us. pysqlite otherwise emits its own
            # BEGIN lazily and — the part that matters — treats a `SAVEPOINT` as
            # the outermost transaction, so **releasing the outermost savepoint
            # commits immediately**. The caller's later `rollback()` then undoes
            # only what came after it, and a write that was supposed to be
            # provisional survives the failure.
            #
            # That is not theoretical: the order service writes an event inside a
            # savepoint and then evaluates the risk gate, and if the gate raised,
            # the VALIDATING event was committed while its projection was rolled
            # back — leaving an order at NEW with a transition in its log that the
            # log is supposed to be the source of truth for. Measured directly:
            # `tests/test_appdb_engine.py::test_a_released_savepoint_is_undone_by_a_later_rollback`.
            dbapi_conn.isolation_level = None

        @event.listens_for(engine, "begin")
        def _on_begin(conn) -> None:  # pragma: no cover - driver hook
            # With `isolation_level = None` the driver no longer opens the
            # transaction, so we do. This is the documented pairing: without it
            # every statement would autocommit and savepoints would be pointless.
            conn.exec_driver_sql("BEGIN")

    @property
    def session_factory(self) -> sessionmaker[Session]:
        if self._session_factory is None:
            self._session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        return self._session_factory

    # --------------------------------------------------------------- schema
    def prepare(self) -> None:
        """Create the database schema from the current metadata.

        This does not alter an existing schema. Schema changes belong in an
        explicit migration, not in application startup.
        """
        if not self._prepared:
            app_metadata.create_all(self.engine)
            self._prepared = True

    @contextlib.contextmanager
    def session(self) -> Iterator[Session]:
        """Transactional session. Commits on clean exit, rolls back on raise."""
        self.prepare()
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # --------------------------------------------------------------- probes
    def health(self) -> bool:
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:  # noqa: BLE001 - a health probe must never raise
            return False

    def user_count(self) -> int:
        """How many accounts exist. Drives first-run bootstrap."""
        self.prepare()
        with self.engine.connect() as conn:
            return int(conn.execute(select(func.count()).select_from(users)).scalar_one())

    def dispose(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
            self._session_factory = None
            self._prepared = False


@lru_cache(maxsize=1)
def get_app_db() -> AppDatabase:
    return AppDatabase()


def reset_app_db_cache() -> None:
    """Dispose the cached singleton and forget it. Tests use this."""
    with contextlib.suppress(Exception):
        # Best effort: teardown must not mask the test's own failure with a
        # secondary one from a half-open engine.
        get_app_db().dispose()
    get_app_db.cache_clear()


__all__ = ["AppDatabase", "get_app_db", "reset_app_db_cache", "utcnow"]
