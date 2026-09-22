from __future__ import annotations

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from lead_agent.infrastructure.db.base import Base
from lead_agent.settings.config import Settings, get_settings


def build_engine(settings: Settings | None = None) -> Engine:
    active = settings or get_settings()
    if active.database_url.startswith("sqlite"):
        return create_engine(
            active.database_url,
            pool_pre_ping=True,
            connect_args={"check_same_thread": False},
        )
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
