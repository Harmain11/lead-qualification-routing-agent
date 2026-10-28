"""Read-only schema compatibility checks; never migrate at startup."""

from sqlalchemy import inspect
from sqlalchemy.engine import Connection

from lead_agent.infrastructure.db import models  # noqa: F401
from lead_agent.infrastructure.db.base import Base


def require_current_schema(connection: Connection) -> None:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in tables:
            raise RuntimeError("Required database schema is missing; promote the schema before startup.")
        columns = {column["name"] for column in inspector.get_columns(table.name)}
        if not set(table.columns.keys()).issubset(columns):
            raise RuntimeError("Required database columns are missing; promote the schema before startup.")
