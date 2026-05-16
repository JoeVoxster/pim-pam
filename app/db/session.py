from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


DEFAULT_DATABASE_URL = "postgresql+psycopg://pim:pim@localhost:5432/pimdb"


def create_engine_from_env(database_url: str | None = None) -> Engine:
    url = database_url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    connect_args: dict[str, object] = {}
    if url.startswith("sqlite"):
        connect_args["timeout"] = 30
    engine = create_engine(url, future=True, pool_pre_ping=True, connect_args=connect_args)
    if url.startswith("sqlite"):
        _configure_sqlite(engine)
    return engine


def get_session_factory(database_url: str | None = None) -> sessionmaker[Session]:
    engine = create_engine_from_env(database_url)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)


def get_database_url(database_url: str | None = None) -> str:
    return database_url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


@contextmanager
def session_scope(database_url: str | None = None) -> Iterator[Session]:
    factory = get_session_factory(database_url)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _configure_sqlite(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
            except Exception:
                # Fall back gracefully when the current connection is read-only.
                pass
            cursor.execute("PRAGMA busy_timeout=30000")
            try:
                cursor.execute("PRAGMA synchronous=NORMAL")
            except Exception:
                pass
        finally:
            cursor.close()
