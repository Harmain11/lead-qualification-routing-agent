# Calling and GoHighLevel connections

These are workspace connections in Corral Integrations, not published vendor marketplace
apps. Enter keys in the app's secure setup form, never in chat or source control. Select
Verify connection after storing credentials. Verification checks approved account/resources
without placing a call or creating a contact. HTTPS is required for a public deployment;
localhost is the local preview. Provider accounts and calling entitlements are still needed.

| Provider | Required account setup | Implemented action |
| --- | --- | --- |
| GoHighLevel | Sub-account Private Integration token, location ID, contacts.readonly, contacts.write and locations.readonly | Verify location/contact search; opt-in contact import; explicit contact upsert/update through CRM sync |
| Twilio | Account SID, Auth Token, owned voice-enabled caller number | Ring the rep's entered phone, then bridge to the stored lead number; read recent matching calls |
| Aircall | API ID/token, user ID and number ID; available user signed in to Aircall Workspace | Request a call from that user; read recent matching calls and provider recording link |
| RingCentral | Private REST developer app, client ID/secret, user JWT, RingOut/ReadCallLog/ReadAccounts permissions, rep phone | RingOut two-leg call; read matching call-log records |
| Vapi | Private API key, configured assistant ID and phone-number ID | Start the saved assistant; import matching recent calls, transcript and recording link when available |
| Retell AI | API key, published agent ID and owned/imported caller phone number | Start saved agent call; import matching recent calls, transcript and recording link when available |

RingCentral needs a developer-app setup. The other five use account API credentials or a
private integration token for this connection; they do not require a shared Corral OAuth app.
No app, phone number or voice agent is automatically purchased or created. Configure those
resources and any provider recording settings in the provider account first.

## Calling from a lead

Open Lead details → Overview → Calls & voice activity. Select a verified connection,
enter your rep number when required, confirm the individual call and choose Call lead or
Start voice-agent call. The destination comes from that lead's stored international number.
Lead editing and agent-action permissions are required. Consent/do-not-contact and residency
policies are checked at the actual outbound boundary. Sequences continue to prepare drafts
for rep approval and do not trigger calls.

Every submission is claimed durably before calling the provider. Repeating its request UUID
returns the existing receipt. A timeout is marked uncertain; Corral never automatically
redials. Check the provider log before confirming another call after an uncertain result.
Aircall returns an empty success response, so its submission receipt has no provider call ID.
Twilio rep-leg and RingOut submission IDs can differ from customer-leg/history IDs; those
submission receipts and subsequent call-history records can appear separately.

Sync recent calls reads bounded provider results (normally latest 50, Twilio up to 100 across
two directions), then keeps only exact normalized matches to the selected lead phone.
Provider call IDs deduplicate history; records cannot be silently moved to another lead.
This is on-demand sync, not a live webhook feed or a full historical backfill. Importing
available transcripts does not automatically run a coaching or qualification assessment.

Aircall, Vapi and Retell recordings are external provider links, opened only by the user.
No returned URL is fetched by the Corral server. Twilio/RingCentral authenticated recordings
and speech-to-text are not exposed by this release. Recording/transcript availability and
link expiration follow the provider account and settings.

## GoHighLevel contact sync

Enable inbound sync explicitly after verification. Contacts require a name and email for
Corral's lead pipeline; incomplete records are reported rather than inventing contact data.
Search is scoped to the configured location, paginated and source-fingerprinted. Completed
runs rescan the source, preserving deduplication while observing changes. Existing inquiry
and enrichment fields are retained during contact-only updates. Opt-out flags add consent
denials before processing; removing the remote flag does not silently grant local consent.

Manual CRM writeback sends name, email, company and supplied phone, updating an imported
contact ID or using HighLevel's account duplicate/upsert rules. It does not replace DND,
tags or unrelated custom fields. Qualification custom-field mapping, opportunities,
HighLevel dialer controls and appointment/conversation sync are outside this connector.

## Operations and verification

Apply migration 0035_lead_calls before running updated API/worker code. Call records are
workspace/lead scoped, included in compliance exports and cascade with lead deletion.
Disconnecting a private credential disables local access and deletes its stored secret;
revoke/rotate the external key in the provider dashboard separately. Account keys are never
returned by the call-history API or included in call/audit payloads.

The adapters and safeguards are covered by isolated HTTP-contract/API tests. They have not
been connected to the user's live vendor accounts, so live calling and recording retrieval
still need a controlled test after secure credential setup.

Official references used:
- https://marketplace.gohighlevel.com/docs/2021-07-28/Authorization/PrivateIntegrationsToken/index.html
- https://marketplace.gohighlevel.com/docs/2021-07-28/ghl/contacts/search-contacts-advanced/
- https://marketplace.gohighlevel.com/docs/2021-07-28/ghl/contacts/upsert-contact/
- https://www.twilio.com/docs/voice/api/call-resource
- https://developers.aircall.io/api-references
- https://developers.ringcentral.com/guide/voice/ringout
- https://docs.vapi.ai/calls/outbound-calling
- https://docs.retellai.com/api-references/create-phone-call
