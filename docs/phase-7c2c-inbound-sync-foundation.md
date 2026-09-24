# Phase 7C2C inbound CRM synchronization foundation

## Scope

This phase adds a provider-neutral inbound CRM synchronization boundary without contacting a
live CRM. Existing outbound Salesforce and HubSpot synchronization remains intact. Salesforce
is the first inbound adapter; HubSpot can implement the same contract in a later phase.

The canonical flow is:

`provider adapter -> canonical inbound lead -> existing source-event pipeline -> analysis ->
workspace ICP -> deterministic qualification -> routing`

There is no separate CRM scoring implementation. New CRM records use the same analysis,
qualification, hard-disqualifier, manual-review, routing, notification, and history boundaries as
other intake channels. Meaningful updates use the same analysis/qualification functions and force
a new routing decision while retaining earlier pipeline/routing history.

## Provider contract

`CrmInboundProvider` keeps acquisition transport provider-specific. It supports bounded record
listing, one-record fetch, opaque cursors, provider normalization, and a next checkpoint. The core
service never interprets Salesforce query locators or future HubSpot cursors.

The Salesforce implementation:

- reads ordinary `Lead` standard fields and does not require enrichment custom fields;
- uses the configured Salesforce API version;
- orders acquisition by `SystemModstamp` and `Id`;
- accepts Salesforce `nextRecordsUrl` pagination;
- emits a stable timestamp/record-ID checkpoint;
- maps Salesforce `LeadSource` into the local source while the durable link records that the lead
  came from Salesforce;
- does not contact a live org until an authorized sync endpoint is invoked in a later certification
  phase.

## Durable state

Migration `0015_crm_inbound_sync` adds three tables:

- `crm_provider_record_links`: workspace/connection/provider/object/record identity to local lead,
  source and enrichment fingerprints, matching strategy, lifecycle state, provider version, and
  write-back origin metadata;
- `crm_inbound_sync_states`: enablement, status, durable successful checkpoint, resume cursor,
  last success, cumulative processed count, and secret-safe error state;
- `crm_inbound_sync_runs`: bounded initial/incremental/manual execution, correlation ID, current
  cursor/checkpoint, timestamps, and fetched/created/updated/unchanged/skipped/failed counters.

The record-link unique constraint includes workspace and provider connection. A provider record ID
cannot create multiple local links inside the same connected account, and the same external ID in
two workspaces remains isolated.

## Initial and incremental behavior

Initial imports are page-bounded and record-bounded. Progress is committed after each processed
record and after each page. A retryable page failure keeps the cursor at the start of that page;
already completed records are safe to replay because provider links and source events are
idempotent. A bounded run stores a resume cursor so the next run continues without loading the
entire CRM into memory.

Incremental and manual runs start from the last successful provider checkpoint. Provider cursor
JSON remains opaque to generic logic. Runs are protected by a per-workspace/connection/object lock
and by durable running state.

## Matching and field ownership

When no provider link exists, an exact normalized email may link a single existing lead. No fuzzy
name matching is performed. Multiple email matches are treated as ambiguous and a separate lead is
created, preventing destructive merges.

The default ownership policy is explicit:

- external CRM owns source/customer fields: name, email, phone, company, title, CRM source,
  provider owner, and other provider facts;
- this application owns intelligence: analysis, ICP/version, component and total scores,
  qualification band, routing, action plan, and recommended action.

Provider ownership/source metadata is stored on the durable link. The local lead retains the
provider's original `LeadSource`; the link identifies Salesforce as the import origin.

## Loop prevention and outbound compatibility

The source fingerprint includes only meaningful provider-owned fields. Provider timestamp changes,
irrelevant metadata, and enrichment-only changes do not re-run analysis or routing. Link metadata
records whether the last observed change was source-owned, unchanged, or our own write-back.

Outbound synchronization records its payload fingerprint and origin on the provider link. An
imported Salesforce Lead is first updated by its actual Salesforce record ID and receives the local
`Lead_Agent_ID__c`; subsequent outbound operations retain the existing deterministic external-ID
upsert behavior. This prevents an imported record from producing a second Salesforce Lead.

## Lifecycle, errors, RBAC, and audit

The exact workspace connection is resolved through the existing authorized connection resolver,
including token refresh and reconnect-required state transitions. Provider errors retain the
existing safe categories for 401, 403, 429, 5xx, timeout, malformed response, and configuration
failure.

Starting inbound synchronization requires `crm.sync`. State/run inspection requires
`integrations.view`; imported lead access continues to use normal lead permissions. Workspace IDs
are applied to connection lookup, state, cursors, record links, lead matching, and API responses.

Provider lifecycle audit events record sync start/completion/failure and aggregate batch counts with
the request correlation ID. Complete CRM payloads and credentials are not written to generic audit
events or logs.

Provider deletion/archive signals update link state (`provider_deleted` or `provider_archived`) and
do not delete local business data.

## API foundation

- `POST /v1/integrations/connections/{connection_id}/inbound-sync`
- `GET /v1/integrations/connections/{connection_id}/inbound-sync/state`
- `GET /v1/integrations/connections/{connection_id}/inbound-sync/runs`

These endpoints provide the backend state needed for later lightweight controls such as inbound
enablement, last sync, imported count, progress, and **Sync now**. Provider-specific polished UI is
deferred to live certification.

## Deferred/live gate

No live Salesforce call is made in this phase. Live initial import remains blocked until the next
certification phase explicitly authorizes it and verifies scopes, API limits, production-size batch
settings, and operator-visible progress. HubSpot inbound acquisition is intentionally not
implemented yet.
