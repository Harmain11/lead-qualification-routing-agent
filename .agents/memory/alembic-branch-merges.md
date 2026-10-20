---
name: Alembic branch merges
description: Keep the application migration graph deployable when schema changes branch.
---

When migrations create separate Alembic heads, add an explicit no-op merge revision whose `down_revision` includes every current head. Keep revision IDs at 32 characters or fewer; the workspace's PostgreSQL `alembic_version.version_num` column is `VARCHAR(32)`.

**Why:** The preview startup runs Alembic against PostgreSQL and failed both on multiple heads and on a merge revision ID that exceeded the version column width.

**How to apply:** Run `alembic heads` before and after adding migrations. Ensure the resulting graph has one head and that every revision identifier fits the version column before restarting the app.
