from __future__ import annotations

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from lead_agent.infrastructure.db.base import Base
from lead_agent.settings.config import Settings, get_settings


def build_engine(settings: Settings | None = None) -> Engine:
    active = settings or get_settings()
    if active.database_url.startswith("sqlite"):
        engine = create_engine(
            active.database_url,
            pool_pre_ping=True,
            # SQLite serializes writers. Public intake can run for several
            # seconds while analysis and provider notifications complete, so
            # a concurrent request must wait for the writer instead of
            # failing immediately with "database is locked".
            connect_args={
                "check_same_thread": False,
                "timeout": max(float(active.database_pool_timeout_seconds), 30.0),
            },
        )
        @event.listens_for(engine, "connect")
        def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            try:
                # WAL keeps the CRM/dashboard reads from blocking an intake
                # writer while analysis, Slack, or Gmail work is in flight.
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute("PRAGMA busy_timeout=60000")
            finally:
                cursor.close()

        return engine
    is_postgres = active.database_url.startswith(("postgresql://", "postgresql+psycopg://"))
    connect_args = (
        {
            "sslmode": active.database_ssl_mode,
            "connect_timeout": active.database_connect_timeout_seconds,
        }
        if is_postgres
        else {}
    )
    pool = (
        {
            "pool_size": active.database_pool_size,
            "max_overflow": active.database_max_overflow,
            "pool_timeout": active.database_pool_timeout_seconds,
            "pool_recycle": active.database_pool_recycle_seconds,
        }
        if is_postgres
        else {}
    )
    return create_engine(
        active.database_url,
        pool_pre_ping=True,
        connect_args=connect_args,
        **pool,
    )


def build_session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=build_engine(settings), autoflush=False, expire_on_commit=False)


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return build_session_factory()


def get_db() -> Generator[Session, None, None]:
    session_factory = get_session_factory()
    with session_factory() as session:
        yield session


def create_all_for_tests(settings: Settings) -> None:
    Base.metadata.create_all(build_engine(settings))
