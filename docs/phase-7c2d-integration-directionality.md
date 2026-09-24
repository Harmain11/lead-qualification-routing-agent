# Phase 7C2D — Integration directionality policy

## Enforced policy

| Integration category | Inbound | Outbound |
|---|---|---|
| Salesforce / HubSpot CRM | Automatic after an authorized admin deliberately enables inbound sync | Manual only through an explicit `crm.sync` command |
| Gmail / Outlook | Automatic inbound source (future acquisition adapter) | Not a mail-sending channel |
| Slack | Not a lead source | Routing notification action only |
| OpenAI | Bounded request | Structured response |

The backend capability registry is authoritative. CRM providers explicitly report
`automatic_inbound=true`, `manual_outbound=true`, and `automatic_outbound=false`.
UI text reflects this policy but does not enforce it.

## CRM inbound scheduling

Connecting or verifying a CRM does not start an import. An actor with both
`integrations.manage` and `crm.sync` must enable inbound synchronization for the
workspace-scoped provider connection. Enablement creates or updates durable state with:

- initial-import completion state;
- a bounded interval (60 seconds to 24 hours);
- last successful checkpoint and resumable cursor;
- last and next synchronization time;
- status, processed-record count, and safe error metadata.

Celery beat dispatches a bounded due-state scan. Each state is claimed by advancing its
next dispatch time before a separate provider job is enqueued. Jobs re-check connection,
workspace, verification, and enablement state. A provider/workspace failure therefore
does not stop other due jobs. The initial mode continues from its durable resume cursor
until complete; later jobs use incremental mode and the opaque provider checkpoint.

Disabling synchronization stops future acquisition and preserves imported leads,
qualification history, record mappings, checkpoints, and audit history.

## CRM outbound commands

No lead creation, source intake, AI analysis, ICP evaluation, qualification, routing,
assignment, review, recalculation, or inbound synchronization code imports or invokes the
CRM writer. Outbound writes are reachable only through:

- explicit single-lead send/update;
- explicit retry;
- confirmed, bounded bulk send/update (maximum 100 unique lead IDs);
- explicit invocation of the `crm.create_or_update_lead` agent tool.

Every command requires `crm.sync`, is workspace scoped, reuses provider linkage and
idempotency, and records actor-safe provider lifecycle audit events. `agents.execute`
alone never grants CRM write permission. Imported Salesforce leads update through their
actual Salesforce record ID, while new local leads keep the deterministic external-ID
upsert behavior.

Bulk commands require a backend `confirmed=true` value, return per-record results, and
continue safely after an individual record failure. Repeating a command with an
unchanged canonical payload reuses the prior successful sync instead of calling the
provider again.

## Live-provider boundary

Phase 7C2D uses mocked provider boundaries only. It makes no Salesforce, HubSpot,
Gmail, Outlook, Slack, or OpenAI request and creates no live provider record.
