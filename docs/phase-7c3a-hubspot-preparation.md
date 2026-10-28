# Phase 7C3A — HubSpot preparation

This phase adds HubSpot Contacts to the existing provider-neutral CRM inbound path. No HubSpot
account was opened, no credentials were used, and no live HubSpot request was made.

## Object model

Use **Contacts only** as the imported lead object. A Contact’s standard `company` field is enough
for the canonical Lead’s company string. A Company object is not independently imported as a lead,
and Deals are not needed by the qualification/routing domain. Existing outbound Company/Deal adapter
support remains available as an explicit opt-in in CRM config, but it is disabled in the example
configuration so the default minimum scope set does not include those objects.

The provider link key is `(workspace_id, provider_connection_id, provider="hubspot",
object_type="contacts", provider_record_id=HubSpot Contact ID)`. Exact-email matching is delegated to
the shared inbound service only when there is exactly one matching local lead; ambiguous matches
remain separate. Re-imports use the durable provider link and source fingerprint.

## Mapping

Inbound HubSpot Contact properties → canonical source fields:

| HubSpot property | Canonical field |
| --- | --- |
| `firstname`, `lastname` | `full_name` |
| `email` | `email` (normalized lowercase) |
| `phone` | `phone` |
| `company` | `company_name` |
| `jobtitle` | `job_title` |
| `hs_analytics_source` | original `source` and `source_detail` |
| `description` | canonical message/context |
| `hubspot_owner_id` | provider metadata only |
| `createdate`, `updatedAt`, `hs_lastmodifieddate` | provider timestamps/checkpoint |

The adapter requires non-empty full name, email, and company, matching the existing canonical
lead requirements. It does not request or write custom properties for inbound acquisition.
Every imported Contact enters the existing `process_source_event` / `reprocess_lead` pipeline;
there is no HubSpot scoring branch. CRM-owned identity/source fields win on meaningful inbound
changes; application qualification/intelligence is not overwritten by a broad last-write-wins merge.

For explicit manual Contact create/update only, the example config maps these minimum custom
properties (create them as Contact properties in the HubSpot test portal, or change the mappings):

`requested_service`, `lead_summary`, `qualification_score`, `qualification_band`, `icp_score`,
`intent_urgency_score`, `engagement_score`, `icp_version`, `recommended_action`, `lead_agent_id`.

`lead_agent_id` is informational; duplicate prevention for the existing Contact upsert continues to
use standard unique `email`. Imported Contact IDs are stored in the provider-record link table.
These intelligence properties are emitted only during explicit Send/Update CRM actions and are
never emitted by inbound processing, scoring, qualification, or routing.

## OAuth and provider lifecycle

- Begin route: `POST /v1/integrations/hubspot/connect`.
- Callback route: `GET /v1/integrations/hubspot/oauth/callback`.
- Exact registered redirect URI: `{INTEGRATION_PUBLIC_BASE_URL}/v1/integrations/hubspot/oauth/callback`.
  The HubSpot project registers localhost callbacks on ports 8000 and 8001; the currently running
  local API uses `http://localhost:8001/v1/integrations/hubspot/oauth/callback`. Production must
  use its actual externally reachable HTTPS base URL. Register the exact deployment URL,
  including path.
- Authorization URL: `https://app.hubspot.com/oauth/authorize`.
- Code exchange and refresh: `POST https://api.hubspot.com/oauth/{HUBSPOT_OAUTH_API_VERSION}/token`
  using form-encoded parameters. The current configured version is `2026-09`.
- PKCE: the shared connection lifecycle uses S256 (`code_challenge`/`code_verifier`) for the OAuth
  start/callback exchange. HubSpot’s generic CRM public-app OAuth guide does not explicitly document
  PKCE for this flow; their distinct MCP OAuth flow does require it. Verify this compatibility during
  the first authorized test-account setup. If HubSpot rejects the verifier, add a provider-specific
  capability to the shared broker rather than weakening other providers’ PKCE.
- Tokens are encrypted through the existing workspace-scoped `SecretStore`; only a secret reference
  is stored in the connection row. Refresh-token rotation is persisted by the shared lifecycle.
- Verification introspects the OAuth access token at
  `POST https://api.hubspot.com/oauth/{version}/token/introspect`, checks it is active and has both
  Contacts scopes, then performs a read-only `GET /crm/v3/objects/contacts?limit=1&properties=email`
  as the actual Contacts API permission probe. Merely storing tokens does not mark a connection
  verified.
- Disconnect first disables the local connection and removes local secret access, then attempts
  remote refresh-token revocation at `POST https://api.hubspot.com/oauth/{version}/token/revoke`.
  Local disconnect remains authoritative if remote revoke is unavailable.

### Required OAuth scopes

Request exactly the HubSpot OAuth group plus the minimum Contacts permissions:

- `oauth` — required by the deployed HubSpot Projects OAuth configuration as its OAuth scope group.
- `crm.objects.contacts.read` — initial and incremental inbound Contacts plus the read-only probe.
- `crm.objects.contacts.write` — explicit manual Contact create/update.

The `oauth` scope is a platform authorization requirement, not an additional CRM object permission.
The connection validator still requires the two Contacts scopes for app functionality.

Do not request Company or Deal scopes for the Contacts-only default. They are only needed if a
workspace deliberately enables the existing optional Company/Deal outbound behavior. No webhook
scope is requested; current acquisition uses the shared scheduled bounded poller.

## Exact later setup and certification sequence

Use a HubSpot **app developer account** plus an isolated **developer test account** (not a production
portal). Create a current Developer Platform app with OAuth and allowlist the test account; do not
start a new legacy public app because HubSpot has ended new legacy-app creation. Use the app’s
OAuth-capable distribution/configuration and register the exact callback above. The app developer
account itself is not the CRM data account to install/test against.

Current HubSpot references: [OAuth 2026-09 token management](https://developers.hubspot.com/docs/api-reference/latest/authentication/manage-oauth-tokens),
[developer platform basics and test account model](https://developers.hubspot.com/developer-platform-basics),
and [legacy public app support/creation status](https://developers.hubspot.com/docs/guides/apps/public-apps/overview).

1. Confirm the deployed `INTEGRATION_PUBLIC_BASE_URL` and external callback reachability.
2. Create or sign in to a HubSpot app developer account.
3. Create one isolated developer test account in that developer portal.
4. Create a current Developer Platform app configured for OAuth; allowlist the test account.
5. Register `{INTEGRATION_PUBLIC_BASE_URL}/v1/integrations/hubspot/oauth/callback` exactly.
6. Configure `oauth`, `crm.objects.contacts.read`, and `crm.objects.contacts.write`, with no Company/Deal scopes.
7. Put the app Client ID in `HUBSPOT_CLIENT_ID` and Client Secret in `HUBSPOT_CLIENT_SECRET` in the
   deployment secret manager; never paste either into chat, source, or browser-visible config.
8. Set `HUBSPOT_OAUTH_API_VERSION=2026-09` and `HUBSPOT_API_BASE_URL=https://api.hubapi.com`.
9. Keep OAuth broker set to `provider`; workspace access/refresh tokens remain application-owned,
   workspace-scoped encrypted secrets.
10. Start/reload the API and worker after server-side environment changes.
11. Connect HubSpot from the target workspace as a member with `integrations.connect`.
12. Authorize the test portal and confirm the callback returns to the local integrations page.
13. Run Verify; assert the connection is verified only after active-token introspection and Contacts
    read probe succeed.
14. Enable automatic inbound synchronization; this creates a Contacts sync state, no CRM record.
15. Confirm the bounded initial job and run/correlation metadata in the application.
16. Directly in HubSpot test account, create one artificial Contact with first/last name, `.example`
    email, company, title, phone, and a source value. Do not create it through our app.
17. Wait for the scheduled worker (do not press Sync Now) and verify one linked local lead enters the
    standard qualification/routing/history pipeline.
18. Edit the Contact’s title in HubSpot; wait for scheduled incremental import and verify update of
    the same local lead/Contact mapping, with no duplicate.
19. Explicitly Send to HubSpot for a separate synthetic local-only lead; verify one Contact and
    repeat Send/Update to verify the same Contact ID is reused. Check that local create/score/route
    never writes to HubSpot.
20. Verify post-write inbound echo is unchanged/own-writeback with no second outbound call; disable
    and re-enable inbound, confirming only the scheduler pauses/resumes and existing local history
    and link survive. Disconnect and confirm local access is removed and remote revoke result is
    recorded without logging tokens.

## Setup values and current limits

Environment names read by code: `HUBSPOT_CLIENT_ID`, `HUBSPOT_CLIENT_SECRET`,
`HUBSPOT_OAUTH_API_VERSION`, `HUBSPOT_API_BASE_URL`, `INTEGRATION_PUBLIC_BASE_URL`,
`OAUTH_BROKER`, and optional development fallback `HUBSPOT_ACCESS_TOKEN`. Do not configure a
workspace token in tracked `.env.example`; production connections store access/refresh credentials
per workspace in the encrypted secret store. HubSpot access tokens expire quickly; the shared
provider lifecycle refreshes them before use and persists rotated refresh credentials.

Inbound and UI use the shared `crm.sync` permission for user-triggered sync, normal lead permissions
for viewing leads, and integration permissions for connect/disconnect/configuration. Scheduled
acquisition runs under `system:crm-inbound-scheduler`. All link/state/run queries remain workspace
scoped. Salesforce/HubSpot outbound adapter calls stay behind explicit manual send/update/retry
commands; inbound import and lead processing have no outbound call path.

HubSpot archived Contacts may be returned by direct record fetch and are safely marked archived by
the shared link model. Regular list/search does not request archived objects, and no provider delete
is translated into destructive local lead deletion.
