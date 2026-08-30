from collections.abc import Generator
from contextlib import contextmanager

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.event import listens_for
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.session import Session

from mealie.core.config import get_app_settings

settings = get_app_settings()


@listens_for(Engine, "connect")
def set_sqlite_pragma_journal_wal(dbapi_connection, connection_record):
    """
    Automatically enables SQLite's WAL journal mode if the setting is activated.

    This is a persistent setting, so turning it off down the line doesn't revert back to the original journal mode.

    Write-Ahead-Log enables sqlite to be used concurrently by multiple readers and writers (writes still happen
    sequentially).
    """
    global settings
    if settings.DB_ENGINE != "sqlite" or not settings.SQLITE_MIGRATE_JOURNAL_WAL:
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def sql_global_init(db_url: str):
    connect_args = {}
    pool_kwargs = {}
    if "sqlite" in db_url:
        connect_args["check_same_thread"] = False
    else:
        # SQLAlchemy's defaults (pool_size=5, max_overflow=10) let a single
        # instance open up to 15 connections on its own -- enough to exhaust
        # a shared Postgres connection-pooler's entire budget by itself under
        # a burst of concurrent requests (e.g. a client reordering many
        # shopping-list items fires one update per item). Capped low and
        # configurable via env vars so an instance can never again be the
        # sole cause of exhausting a shared pooler; raise these only if
        # deployed against a pooler/database with real headroom to spare.
        import os

        pool_kwargs = {
            "pool_size": int(os.environ.get("MEALIE_DB_POOL_SIZE", "3")),
            "max_overflow": int(os.environ.get("MEALIE_DB_MAX_OVERFLOW", "2")),
            "pool_timeout": int(os.environ.get("MEALIE_DB_POOL_TIMEOUT", "10")),
        }

    engine = sa.create_engine(
        db_url, echo=False, connect_args=connect_args, pool_pre_ping=True, future=True, **pool_kwargs
    )

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)

    return SessionLocal, engine


SessionLocal, engine = sql_global_init(settings.DB_URL)  # type: ignore


@contextmanager
def session_context() -> Generator[Session, None, None]:
    """
    session_context() provides a managed session to the database that is automatically
    closed when the context is exited. This is the preferred method of accessing the
    database.

    Note: use `generate_session` when using the `Depends` function from FastAPI
    """
    global SessionLocal
    sess = SessionLocal()
    try:
        yield sess
    finally:
        sess.close()


def generate_session() -> Generator[Session, None, None]:
    """
    WARNING: This function should _only_ be called when used with
    using the `Depends` function from FastAPI. This function will leak
    sessions if used outside of the context of a request.

    Use `with_session` instead. That function will allow you to use the
    session within a context manager
    """
    global SessionLocal
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
