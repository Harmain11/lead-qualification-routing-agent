# Phase 7D failure and recovery runbook

This runbook is for the Lead Qualification & Routing Agent deployment. It is
portable across local development and the eventual Linux/Replit deployment.
It does not require changing provider credentials or deleting checkpoints.

## First response

1. Record the UTC time, workspace, provider, operation, and request/correlation
   ID from the structured logs.
2. Check API liveness and readiness:

   ```text
   GET /health/live
   GET /health/ready
   ```

3. Check the provider connection status and inbound-sync state in the workspace
   UI/API. Do not reset `last_successful_cursor` or manually mark a failed run
   completed.
4. Confirm PostgreSQL, Redis, the Celery worker, and Celery beat are running.
5. If the provider is unavailable, allow the bounded retry schedule to run. A
   failed run retains its durable resume cursor and does not acknowledge the
   provider page.

## Provider token expiry or revoked credentials

- A 401/revocation transitions the connection to a safe reauthorization state;
  it does not issue CRM writes or advance an inbound checkpoint.
- For OAuth providers, reconnect through the normal OAuth flow. The callback
  is actor/workspace-bound, one-time, and PKCE-bound where supported.
- For a refresh-token failure, reconnect instead of replacing a secret in the
  database. The secret store owns credential material; logs and audit records
  contain only safe categories and references.
- After reconnecting, verify the connection, then use **Sync Now** or wait for
  the next scheduled run. Reprocessing is idempotent by provider record and
  source event.

## Provider outage, timeout, 429, or 5xx

- Gmail, Salesforce, and HubSpot classify transport, timeout, rate-limit, and
  server failures as retryable where appropriate. 401, missing scope,
  malformed data, invalid history, and missing resources are terminal or
  recovery-specific errors.
- Do not advance a checkpoint during a failed fetch or while a page is still
  being processed. A retry resumes from the last committed page boundary.
- Slack retries honor `Retry-After` and reuse the same notification record and
  deterministic `client_msg_id`. A channel or membership error is terminal
  until the channel configuration is corrected.
- A provider failure is isolated to that workspace/provider; healthy provider
  schedules continue.

## Invalid Gmail history checkpoint

Gmail history expiration is treated as a reconciliation condition. The next
read falls back to the bounded recent-message listing, preserves the last
durable state until records are processed, and then stores the new history ID.
If reconciliation fails, leave the state failed and retry later; do not set a
new history ID by hand.

## Failed Celery job or worker restart

- Celery uses late acknowledgements, rejects tasks when a worker is lost, and
  limits retries with the configured job retry bound.
- Inbound sync ownership is a PostgreSQL lease, not a process-local lock.
  Another worker may take an expired lease, mark the old run `abandoned`, and
  resume from `resume_cursor`/`last_successful_cursor`.
- A worker crash after fetch or after local processing can cause safe replay.
  Source-event, provider-record-link, routing, CRM-sync, and notification
  boundaries must return existing durable records rather than create a second
  side effect.
- Restart beat once if required. The durable schedule state remains in
  PostgreSQL; beat does not own provider checkpoints.

## Redis outage

Redis is used for broker/result and shared rate limiting where configured. If
it is unavailable:

- `/health/ready` reports not ready when Redis is required;
- requests fail safely where the Redis-backed dependency is required;
- scheduled work may wait in the broker, but PostgreSQL sync state and
  checkpoints remain durable and are not silently advanced;
- restore Redis, restart the affected worker/beat processes if necessary, and
  inspect due/failed sync states before using **Sync Now**.

Do not move checkpoints, leases, provider links, or notification state into
Redis.

## PostgreSQL outage

Stop accepting writes while readiness is failing. A transaction that cannot
commit must not acknowledge provider progress. Restore PostgreSQL, verify the
migration head, then inspect failed/running inbound states. Expired leases are
recovered by the next worker; do not delete running rows or reset cursors.

## Notification delivery failure

The routing decision and pending notification are committed before the Slack
request. A retry updates that same notification row, honors `Retry-After`, and
stops after the bounded retry limit with a terminal `failed` state. Fix the
token/channel/membership issue or the Slack outage, then use the notification
retry operation. Never send a second ad-hoc message from the provider console.

## Audit and redaction checks

Operational evidence should include provider, operation, workspace/connection,
correlation ID, attempt, retryability, and safe error category. It must never
include access/refresh tokens, API keys, client secrets, encryption keys,
authorization headers, or raw email/CRM message bodies. If a log appears to
contain one of these values, stop the investigation and rotate the exposed
credential through the approved account process.

## Safe recovery checklist

- [ ] Provider connection is verified and active.
- [ ] PostgreSQL and Redis readiness is healthy.
- [ ] No non-expired inbound lease is held by another worker.
- [ ] Failed run has a resume cursor and safe error category.
- [ ] Re-running produces one provider link, one logical routing decision, and
      one notification record.
- [ ] No automatic CRM outbound operation was triggered by the inbound retry.
- [ ] Correlated audit entries contain no secrets or raw message body.
- [ ] Do not deploy to Replit or begin performance work as part of recovery.
