---
name: Reasoning backfill progress
description: Durable job progress and completed-analysis idempotency for workspace reasoning backfills.
---

Persist reasoning backfill progress and per-lead configuration receipts as provider lifecycle audit events; use the job correlation ID to retrieve the latest counters. The configuration key includes provider, model, and prompt version. A Redis lock prevents concurrent jobs for the same workspace connection and configuration.

**Why:** Progress must survive page refreshes and worker restarts, while backfills must not repeat completed model calls. The append-only lifecycle audit already provides durable workspace-scoped history without replacing analysis records or introducing a second job-state table.

**How to apply:** When extending backfill status or retry behavior, preserve job correlation IDs and total/completed/failed/skipped counters; only treat a lead/configuration receipt as complete after analysis persistence succeeds.
