# Phase 7C provider certification gates

This document defines the shared lifecycle completed in Phase 7C1 and the gates each
provider phase must satisfy. The detailed baseline remains
[`phase-7c0-real-provider-readiness-audit.md`](phase-7c0-real-provider-readiness-audit.md).

## Shared connection lifecycle

Workspace-owned connections move through a deliberately small lifecycle:

1. `disconnected`: no usable local secret reference.
2. `awaiting_authorization`: OAuth state and PKCE verifier exist, but no provider token does.
3. `connected`: credentials are stored and structurally usable, but are not remotely verified.
4. `verified`: a provider-specific authenticated, low-risk probe succeeded.
5. `expired` or `needs_reauthorization`: the token cannot be refreshed or the provider rejected it.
6. `error`: verification or configuration failed without proving the credential was revoked.

The UI and API never equate “credentials stored” with “verified.” A provider without a
real validator returns `provider_validation_not_implemented` and fails closed. Provider
certification must add a low-risk probe and controlled mocked fixtures before the state can
become `verified`.

## Secret ownership and storage

Operator configuration owns OAuth client IDs, OAuth client secrets, webhook/signing secrets,
and the encrypted-store master key. These values are deployment inputs and are never entered
by workspace users.

Workspace secret storage owns access tokens, refresh tokens, provider account credentials,
and token metadata. Production may use the included cloud-neutral encrypted database backend
(`lead_agent.infrastructure.encrypted_secrets:create_backend`) or another implementation of
the managed secret backend port. The included backend uses AES-256-GCM with random nonces,
workspace/provider/key-version binding as authenticated data, opaque `dbsecret://` references,
and deployment-supplied current and previous keys. Database rows contain ciphertext, never
plaintext. Rotation is performed by adding the old key to `SECRET_STORE_PREVIOUS_KEYS`, setting
a new active key/version, and replacing credentials so they are re-encrypted under the active
version. Deployment must keep master keys outside the database and retain old keys until all
records have rotated.

Secret reads and replacements require the expected workspace and provider. A copied reference
from another workspace or provider resolves to no credential. API responses, lifecycle audit
details, and provider errors contain no credential values or raw provider bodies.

## Runtime resolution and refresh

`WorkspaceAuthorizedConnectionResolver` is the common runtime boundary for CRM now and Slack
in Phase 7C4. It resolves only an active `connected` or `verified` connection in the requested
workspace. Callers must enforce the operation permission (`crm.sync` for CRM); the resolver also
offers a workspace-context method that checks an explicit required permission.

For OAuth credentials it loads the scoped secret, checks `token_expires_at` with a refresh skew,
refreshes when required, preserves or rotates the refresh token, atomically replaces the scoped
secret, updates expiration metadata, and records a correlated audit event. A per-connection lock
prevents duplicate refreshes inside one application process and the database row is selected for
update for multi-worker coordination. Provider-specific distributed behavior must still be
tested against PostgreSQL during certification. Runtime resolution never falls back to another
workspace or to global CRM credentials in production.

Tokens without provider expiration metadata are not assigned a synthetic expiry. This is
important for non-rotating Slack tokens and still permits future Gmail and Microsoft delegated
refresh-token flows.

## Validation, errors, and correlation

`ProviderConnectionValidator` receives only the provider, resolved credentials, and consented
scopes. Implementations must use an authenticated, non-mutating provider request and return a
structured result: connected, invalid credentials, expired/revoked, insufficient permissions,
rate limited, temporarily unavailable, malformed response, or configuration error.

The connection-test endpoint is workspace scoped, protected by `integrations.manage`, limited by
the existing provider-action rate limit, and persists a lifecycle audit event. Audit events carry
the existing request correlation ID, actor, workspace, connection, provider, outcome, safe error
code, and non-sensitive details. The standard provider error translator covers 400, 401, 403,
404, 409, 429, 408/425, 5xx, timeout, malformed responses, and revoked credentials without
persisting raw response bodies.

## Disconnect and revocation

Disconnect first removes the local reference, marks the connection inactive, and commits that
authoritative local state. `ProviderRevoker` then attempts provider-specific remote revocation
when supported. A failed or unsupported remote revoke is captured in the lifecycle result and
audit event, but cannot reactivate the local connection. Secret cleanup is attempted only after
a workspace/provider-scoped read, preventing a swapped reference from deleting another
workspace's credential.

## Credential model decisions

- Salesforce, HubSpot, Slack, Gmail, and Outlook connections are workspace owned. OAuth client
  credentials remain operator owned.
- OpenAI remains operator owned for this release. The runtime reads the deployment-provided
  project key and approved model; the Integrations card is informational and exposes no key-entry
  control. Phase 7C7 may revisit workspace-owned keys only as an explicit product decision.
- Slack runtime wiring and workspace channel selection remain Phase 7C4 work. It must consume the
  common resolver and must not use a temporary global-token fallback.
- Gmail and Outlook acquisition, polling/delta/subscription behavior, and mailbox checkpoints are
  intentionally deferred to their provider phases. The shared OAuth lifecycle supports delegated
  refresh tokens without selecting an acquisition mechanism.

## Provider-specific certification still required

Before any provider can be called certified, its phase must add and test: the real OAuth broker
configuration, low-risk validator, remote revoker where the provider supports one, safe mapping
of provider-specific error payloads and retry metadata, exact scopes, account identity, token
expiry/rotation semantics, and PostgreSQL concurrency behavior. Salesforce and HubSpot must also
complete the schema and real read-after-write checks from the 7C0 audit. No provider account,
application, credential, mailbox acquisition adapter, real CRM write, or Slack message was
created in Phase 7C1.

