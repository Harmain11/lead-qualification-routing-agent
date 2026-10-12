# Corral feature status — 8 October 2026

This is the implementation in the attached codex/corral-local-dev worktree,
based on GitHub main 79f1f1c. The original voice/dialer scope remains deferred.
Open the running preview at http://localhost:5173.

| Area | Ready in this checkout | Operation and limits |
| --- | --- | --- |
| Qualification | Won/lost association analysis, evidence and proposed ICP weight changes | At least 10 wins and 10 losses under the current ICP. Approval creates an unpublished draft; review/publish through existing ICP controls. Historical scores remain recorded. |
| Routing / Team | Ordered territory groups, region/industry/service matching, group SLA, member lists, capacity caps and availability check-ins | Non-matching or unavailable members remain queued. An expiring check-in stops assignments until renewed. Existing routing applies when no territory policy is configured. |
| Agents | Researcher, Coach, Watcher and Playbook capability registry; stored reports and activity | Monitoring/research/digest generation is opt-in. Watcher uses recorded email/progress evidence and preserves the original inquiry. It does not acquire website-visit telemetry. |
| Follow-ups | Durable sequences, wait windows, reply pauses, draft history integration, suggested contact-local timing | Timing uses recent business-hour replies when at least three exist; otherwise configured time. Every outgoing follow-up still needs rep approval/send through existing controls. |
| Contacts / Companies | Independent people/company records, exact-email intake links, company rollups and touch history, search and explicit duplicate merges | Existing unindexed leads can be linked with the Records backfill action, 500 per batch. Independent records and merge aliases are preserved. |
| Analytics | Current USD pipeline/monthly closed revenue, historical expected value, quota coverage, channel costs/ROI, cohorts and contextual rep comparisons | Cohort analysis is bounded to 5,000 leads with a visible truncation indicator. Forecast/fairness values are withheld below sample thresholds. Costs are entered per source/month; currencies are kept separate. |
| Playbooks | Visual editable canvas, keyboard positioning, conditional branches, saved snapshots, approval/independent-review tiers and operational trust score | Cycles and disconnected nodes are rejected. Trust never grants permissions or sending authority. Enrolled runs keep their specification/type when the definition changes. |
| Enrichment / Competitive support | Reviewed company website fetch, citation-checked OpenAI facts, manager approval, company profile enrichment, competitor battlecards and merge suggestions | Public HTTPS only; private addresses/redirects/oversized responses are rejected. Funding/news/hiring facts remain unknown unless the reviewed page supports them. |
| Coaching | Recorded-text scorecards, quoted objections and recorded responses, outcome context, weekly deduplicated digests | Based on acquired email/progress text. Digests require manager approval before rep visibility. Call/audio scoring remains part of the deferred voice work. |
| Collaboration | Ctrl/Cmd+K palette, permission-filtered page/record search, teammate presence and shared notes | Presence refreshes every 15 seconds with a 45-second expiry. Version conflicts preserve the editor text for a manual merge. |
| Governance | Immutable permission/objection ledger, global/channel DNC enforcement, strict consent option, complete per-lead audit export, residency guard and reviewed retention deletion | DNC blocks drafts, follow-up sends, confirmations and meeting emails at their delivery boundaries. Retention requires a current plan and explicit permanent-deletion confirmation; independent contacts/consent are retained. |
| UI | Modular product screens, central light/dark tokens, shared responsive components, branded controls, dialog focus/restoration, canvas keyboard controls and page-based loading | Existing Corral visual identity is retained. The 12 October UI pass covers all 17 screens at 390/768/1440 px in both themes, shared button/form styles, native dialogs and tablet reflow. See UI-QA.md for checks and limits. |
| Agency / Branding | Permission-scoped client overview/switching, product name, logo, accent color and custom-domain setting | DNS, TLS and hosting setup remain external configuration. Workspace data stays permission-scoped. |
| Zapier / Make | HTTPS Create Lead packages, password token fields, read-only connection validation, stable source IDs for retries and redacted intake logging | Local code is prepared and tested. Public hosting URL, vendor developer accounts, installation tests and marketplace review/submission are still needed. |

## Where to work

- Backend operations: backend/src/lead_agent/application/, api/routes/, domain/operations.py.
- Frontend screens: frontend/src/views/. Theme tokens: frontend/src/designTokens.css.
- Shared components: frontend/src/components/operations.css, OperationsDialog, CommandPalette, CollaborationPanel.
- Connector packages: integrations/zapier/ and integrations/make/.
- Windows startup: scripts/local_dev.ps1. Setup and stop/restart commands: LOCAL-DEVELOPMENT.md.

## Validation

- 504 unit/integration tests passed using the normal test cookie names and isolated databases.
- All 37 new operations tests pass, including the final specification-snapshot and timing refinements.
- Four connector tests pass, including official Zapier SDK schema validation.
- Production TypeScript/Vite build passes; initial JS is approximately 409 KB before gzip.
- Changed Python lint checks and Git whitespace checks pass.
- Fresh SQLite migration reaches 0034_operations_leases; database integrity checks pass.
- The 12 original lead identities and inquiry/contact payloads match the pre-change backup.
- API readiness succeeds, and the API, frontend and operations worker run in hidden local processes.

Five upstream Replit startup tests use a Bash harness that fails on this Windows host;
the Windows launcher was verified directly. Live OpenAI/Gmail/CRM calls, marketplace
installation/publication and a deployed PostgreSQL run were not exercised here.
Repo-wide strict mypy still reports 47 existing errors; GitHub main reports 55 under
the same environment. New operations modules introduce no reported type errors.
This implementation is published on the separate GitHub branch codex/corral-local-dev.
GitHub main is unchanged. There has been no production deployment.
The attached worktree remains available for further editing.

## UI consistency update — 12 October 2026

The UI polish and Google sign-in logo changes are documented in [UI-QA.md](UI-QA.md).
TypeScript and the production frontend build pass after this update.
