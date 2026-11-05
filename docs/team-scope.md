# Team scope foundation

Team scope is independent of representative routing. An owner/admin with
`members.manage` assigns workspace membership IDs and lead IDs to explicit teams.
Changing a routing representative does not implicitly grant team access.

## HTTP contract

All endpoints use existing workspace authentication and cookie CSRF protection.

| Method and path | Input | Response |
| --- | --- | --- |
| `GET /v1/teams` | — | `Team[]` |
| `POST /v1/teams` | `{name, description?}` | 201 `Team` |
| `PATCH /v1/teams/{id}` | `{name, description?}` | `Team` |
| `DELETE /v1/teams/{id}` | — | 204; default team returns 409 |
| `PUT /v1/teams/{id}/members/{membership_id}` | — | `Team` |
| `DELETE /v1/teams/{id}/members/{membership_id}` | — | `Team` |
| `PUT /v1/teams/{id}/leads/{lead_id}` | — | `Team` |
| `DELETE /v1/teams/{id}/leads/{lead_id}` | — | `Team` |

`Team` contains UUID strings `id`, `workspace_id`; strings `name`, `description`;
boolean `is_default`; UUID arrays `member_ids`, `lead_ids`; and ISO timestamps
`created_at`, `updated_at`. PATCH replaces both editable fields; omitting
description resets it to an empty string. Names are trimmed and unique per
workspace after case folding. Link PUT/DELETE operations are idempotent. All
mutations create workspace administrator audit events.

Owner/admin reads return all workspace teams. Other roles read only their own
teams, without requiring `members.view`. Lead arrays are filtered through the
request lead scope, including on the default team. Mutations require **both** an
owner/admin role and `members.manage`; a lower role's custom grant is insufficient.
Foreign-workspace IDs return 404. Only active workspace members can be added.

## Schema and enrollment

Revision `0058_workspace_teams` follows `0057_lossless_lead_trash`. It creates team,
team-member and team-lead tables, then creates exactly one default team per
existing workspace and links all active memberships and all existing leads,
including trash. It preserves original records and routing. The default team's
UUID equals its workspace UUID in the separate team table.

The backfill deliberately preserves the previous workspace-wide audience until
admins create explicit teams. A member with explicit teams uses the union of
those teams instead of the compatibility default. With no explicit teams, the
default membership is effective; with no memberships, lead access is empty.
Removing the last explicit membership can therefore restore default visibility
if the member still belongs to the default team. Remove their default membership
when that fallback is inappropriate.

Unit-of-work events create defaults for new workspaces, enroll newly active
members without existing team links, and enroll unscoped intake/admin-created
leads into the default team. Restricted actors' newly created leads are assigned
to their effective teams. Editing an already active member does not undo a manual
team removal. Suspended members cannot authenticate; reactivation retains their
existing team links, or enrolls into the default when there are none.

Apply the migration explicitly to a backed-up target database **before** enabling
the code. No startup migration or production access is introduced here. Downgrade
drops only the three team tables; original leads/members remain intact, but team
assignments are lost. Roll back application code together with the schema.

## Enforcement and limits

`get_workspace_context` binds a scope to the request's SQLAlchemy session before
application queries. Restricted roles use request-snapshotted effective team IDs;
lead assignment links are evaluated in SQL. Membership changes take effect on
the next request. Each session supports one scope and must not be reused across
users/workspaces. Background workers/public intake sessions without an
authenticated workspace context remain trusted and unscoped.

Central loader criteria cover leads; every mapped direct `lead_id` child except
personal insights and team grant links; source events; routing/win receipts;
contacts, companies and consent linked through visible leads; and lead-specific
tool audits. Restricted NULL-lead children are hidden. Polymorphic outbox payloads
are hidden from restricted reads. Scalars, aggregates, subqueries, aliases,
relationships, `get`, `get_one`, refreshes and expired-column loads are covered.
Flush checks verify persisted row access and new lead/relationship references.
Authorized ORM inserts and updates work; hidden-row and relationship retargeting
checks fail with 403.

Restricted sessions reject text/Core SQL, literal SQL columns, Core forms nested
in ORM statements, bulk inserts, bulk writes by primary-key mappings, legacy bulk
methods and direct `session.connection()`. Set-based ORM update/delete statements
receive scope criteria, cannot rewrite scope keys and disable ORM identity-map
synchronization; callers should reload objects after them. Admin Core/text APIs
remain compatible with existing authentication/CRM flows and rely on their
existing explicit workspace predicates.

This is application enforcement, not PostgreSQL row-level security. Code opening
another engine/session or a provider API must carry its own authorization.
Workspace-owned JSON/history, external Gmail/calendar/CRM responses, and private
email drafts are not automatically decomposed into lead scopes. Shared company
and contact records are filtered at record level, not by individual profile
fields. Insight ownership/privacy is deliberately enforced separately by
`application.coaching_privacy.insight_visibility`; do not infer private coaching
ownership from a mutable lead assignment. Services bypassing the request
dependency must explicitly bind scope or enforce their trusted-service boundary.

The tests exercise disposable SQLite stores and API clients. PostgreSQL execution,
query plans at production scale, external provider behavior and browser UI remain
separate validation work. The migration emits portable INSERT SELECT backfills
and can render PostgreSQL SQL offline; no live PostgreSQL migration was run here.
