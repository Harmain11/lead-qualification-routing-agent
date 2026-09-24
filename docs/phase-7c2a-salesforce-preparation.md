# Phase 7C2A — Salesforce certification preparation

This is a code-preparation and operator-instruction document only. No Salesforce account,
External Client App, credential, or live request was created for this phase.

## What the repository actually does

- `POST /v1/integrations/salesforce/connect` creates a workspace-bound state value and PKCE
  verifier, then builds the Salesforce authorization URL.
- The provider OAuth broker uses
  `{SALESFORCE_LOGIN_BASE_URL}/services/oauth2/authorize` and
  `{SALESFORCE_LOGIN_BASE_URL}/services/oauth2/token`.
- The callback is `GET /v1/integrations/salesforce/oauth/callback`. State, actor, workspace,
  provider, expiry, and PKCE are checked before exchanging the authorization code.
- The OAuth exchange stores the workspace access token, refresh token, `instance_url`, and
  expiration metadata in the scoped secret store. Operator client credentials are not stored in
  that workspace secret.
- CRM runtime resolution uses `WorkspaceAuthorizedConnectionResolver`. It refreshes an expired
  Salesforce token once, persists any rotated refresh token, then constructs the adapter with the
  resulting access token and instance URL. The Salesforce adapter no longer performs a second
  refresh exchange during `sync`.
- The adapter upserts `Lead` by a stable application ID, optionally upserting `Opportunity` only
  when explicitly enabled (`sync_opportunity: false` by default). It persists the Salesforce Lead
  ID in `crm_syncs.provider_entity_ids.contact_id` (the existing provider-neutral field name).
- Disconnect clears the local workspace reference first, then the Salesforce revocation adapter
  posts the refresh token (or access token) to `/services/oauth2/revoke`. Local disconnect remains
  authoritative if remote revocation fails.

## Salesforce-specific validation and revocation

`SalesforceConnectionValidator` performs a genuinely authenticated, read-only request:

`GET {instance_url}/services/data/{api_version}/limits`

It requires an access token, an HTTPS Salesforce instance host, and the `api` scope when scopes
are present. HTTP status, timeout, and malformed-response failures are translated through the
shared provider error model. Only a successful response marks the connection `verified`.

`SalesforceProviderRevoker` uses the provider OAuth revoke endpoint. It returns only
`confirmed`, `unsupported`, or `failed` plus a safe error code; token values are never included in
errors or audit details.

## Exact CRM object and field mapping

The current strategy is one Salesforce `Lead`. Contact, Account, and Opportunity synchronization
are not part of the default path. `Opportunity` mappings exist for a later explicitly enabled
feature, but `sync_opportunity` is false.

| Application value | Salesforce field | Notes |
| --- | --- | --- |
| Contact first name | `Lead.FirstName` | Standard Lead field |
| Contact last name | `Lead.LastName` | Standard Lead field |
| Company | `Lead.Company` | Standard Lead field; required by Salesforce |
| Email | `Lead.Email` | Standard Lead field |
| Job title | `Lead.Title` | Standard Lead field |
| Phone | `Lead.Phone` | Standard Lead field when present |
| Source channel | `Lead.LeadSource` | Standard Lead field |
| Requested service | `Lead.Requested_Service__c` | Custom Text(255) |
| Summary | `Lead.Lead_Summary__c` | Custom Long Text Area |
| Qualification band | `Lead.Qualification_Band__c` | Custom Text(40) |
| Total score | `Lead.Qualification_Score__c` | Custom Number(3,0) |
| ICP score | `Lead.ICP_Score__c` | Custom Number(2,0) |
| ICP profile version | `Lead.ICP_Profile_Version__c` | Custom Text(64) |
| Intent score | `Lead.Intent_Urgency_Score__c` | Custom Number(2,0) |
| Engagement score | `Lead.Engagement_Score__c` | Custom Number(2,0) |
| Qualification rationale | `Lead.Qualification_Rationale__c` | Custom Long Text Area |
| Recommended action | `Lead.Recommended_Action__c` | Custom Long Text Area |
| Action plan | `Lead.Sales_Action_Plan__c` | Custom Long Text Area containing JSON |
| Routing queue | `Lead.Assigned_Queue__c` | Custom Text(80) |
| Assigned representative | `Lead.Assigned_Rep_Name__c` | Custom Text(255) |
| Urgency timeline | `Lead.Urgency_Timeline__c` | Custom Text(255) |
| Canonical application lead ID | `Lead.Lead_Agent_ID__c` | Custom Text(36), External ID and Unique |
| Salesforce owner | `Lead.OwnerId` | Standard field only when `owner_mapping` is configured; currently empty |

`Lead_Agent_ID__c` is the only identity field required for deterministic deduplication. The
adapter upserts by `PATCH /sobjects/Lead/Lead_Agent_ID__c/{application_uuid}`. Repeated syncs
therefore update the same Lead rather than fuzzy-matching names or creating a duplicate.

## Human/browser setup values

Recommended External Client App name: `Lead Qualification Routing Agent - Test`.

For a Developer Edition test org, use the `login.salesforce.com` authorization host. For a sandbox,
set `SALESFORCE_LOGIN_BASE_URL=https://test.salesforce.com`. The local callback is exactly:

`http://localhost:8000/v1/integrations/salesforce/oauth/callback`

If staging is used and its API domain has not yet been selected, the callback is:

`https://<API_DOMAIN>/v1/integrations/salesforce/oauth/callback`

Do not replace `<API_DOMAIN>` until the deployment has a real public HTTPS API hostname.

Configure the External Client App for:

- OAuth web-server / authorization-code flow.
- The exact callback URL above.
- PKCE required, using S256. The application generates state and the verifier; Salesforce must
  return the authorization code to the callback.
- Testing distribution: `All users may self-authorize` is the least-friction choice for one
  controlled Developer Edition user. If the org requires admin approval, use the equivalent
  admin-approved-user mode and assign the test user the resulting permission set.
- OAuth scope parameter values: `api` and `refresh_token`. Salesforce's UI may display the
  latter as “Perform requests at any time (`refresh_token`, `offline_access`)”; do not send the
  literal string `refresh_token/offline_access`.

Operator configuration variables:

```text
CRM_PROVIDER=salesforce
SALESFORCE_CLIENT_ID=<operator-managed External Client App client ID>
SALESFORCE_CLIENT_SECRET=<operator-managed External Client App client secret>
SALESFORCE_LOGIN_BASE_URL=https://login.salesforce.com
SALESFORCE_API_VERSION=v67.0
```

`SALESFORCE_REFRESH_TOKEN` remains only an optional development fallback. A real workspace OAuth
connection stores its access token, refresh token, instance URL, and expiration metadata in the
workspace secret store. No client secret is sent to the frontend.

The test Salesforce user needs API access and permission to read/write Lead records and the
custom Lead fields above. If `owner_mapping` is later populated, the integration user also needs
permission to assign the selected Lead owners. No Opportunity permissions are needed for the
default configuration.

## Controlled certification fixture

Use only this synthetic identity:

- Name: Jordan Test
- Company: Phase7 Testing Corp
- Email: `jordan.test@phase7-testing.example`
- Title: VP of Operations
- Industry: SaaS
- Employees: 180
- Geography: United States
- Budget: USD 30,000
- Service: Lead routing automation

Expected local result: 100/100, `hot`, `40 ICP / 35 intent-urgency / 25 engagement`, routed to
`senior_rep`, priority `urgent`, representative `Alex Morgan`. Expected Salesforce result is one
Lead with `Lead_Agent_ID__c` equal to the local lead UUID and the mapped fields above populated.

## Browser-assisted certification sequence

1. Create or sign into a dedicated Developer Edition or approved sandbox.
2. Create the External Client App with the name and settings above.
3. Add the exact callback and scopes.
4. Obtain the client ID and client secret.
5. Store operator credentials in the deployment secret manager; never paste them into the UI or chat.
6. Start the application with the non-secret Salesforce settings.
7. Open Integrations and click Salesforce Connect.
8. Sign in and approve the requested scopes.
9. Run “Verify connection” and confirm `verified` plus a lifecycle audit event.
10. Submit the controlled Jordan Test lead.
11. Run CRM sync and record the returned Salesforce Lead ID.
12. Verify the actual Lead and every mapped field in Salesforce.
13. Change a local lead field and resync.
14. Verify the same Salesforce Lead ID was updated.
15. Sync the unchanged lead again.
16. Confirm no unwanted duplicate exists.
17. Exercise the expired-token/refresh path only with the controlled test connection.
18. Disconnect and confirm the local connection is inactive and CRM sync is blocked.
19. Confirm remote revoke result is recorded without token data.
20. Reconnect through OAuth.
21. Verify the connection again.
22. Sync once more and confirm operation resumes against the intended Lead.

All implementation tests use mocked transports. No live Salesforce request is part of Phase 7C2A.
