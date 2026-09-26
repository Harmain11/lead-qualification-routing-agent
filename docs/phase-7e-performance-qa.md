# Phase 7E — Performance, Frontend, Browser & Mobile QA

Date: 2026-09-26  
Scope: local QA only; no Replit deployment performed.

## Executive result

The application is materially faster and safer for the current local/demo workload. Lead retrieval is now server-filtered and paginated, the browser renders only the requested page, API failures have explicit authentication/permission messaging, deferred integrations are not presented as customer-ready, and the avoidable Vite large-chunk warning is gone.

The result is **PASS**. Exact runtime viewport checks were completed in headless Chrome/Chromium at all five requested sizes. Edge was not directly tested because its executable was unavailable; it is recorded as unverified, not as a blocking certification failure.

## Baseline and environment

- Initial branch: `main`; initial working tree was clean.
- Initial commit: `8e910a3 Complete Phase 7D failure recovery hardening`.
- Initial migration state: `0019_unique_lead_source_event (head)`.
- Postgres and Redis compose services were healthy.
- Backend liveness and readiness both returned HTTP 200; readiness reported PostgreSQL and Redis healthy.
- No schema migration was required. The scheduler due-work indexes were already present in the certified history: `0016` provides the `enabled,next_sync_at` lookup and `0018` provides lease-expiry support. Migration head remains `0019`.

### Frontend build baseline

Before the changes, `npm run build` succeeded but emitted the Vite warning for a chunk larger than 500 kB:

| Asset | Raw | Gzip |
| --- | ---: | ---: |
| Main JS | 311.27 kB | 91.98 kB |
| Lead intelligence network chunk | 917.37 kB | 247.05 kB |
| CSS | 89.32 kB | 18.02 kB |

The large chunk was the optional Three.js/React Three Fiber network effect.

## Defects fixed

### Lead list scalability and API behavior

- Added bounded `limit`/`offset` pagination with a maximum page size of 100.
- Added server-side search, source, qualification-band, and inclusive date-range filtering.
- Added deterministic `received_at DESC, id DESC` ordering.
- Added `total`, `has_more`, `limit`, and `offset` response metadata.
- Added eager relationship loading for qualification, routing/notifications, and CRM state, removing the previous per-row current-state lookup pattern.
- Reworked the React lead view to fetch only the active page, debounce search, cancel stale requests, preserve selection across pages, and show loading/error/retry/pagination states.
- The browser check against the current backend returned 44 records initially and reduced to 1 record for `Priya`, confirming end-to-end server-side filtering.

### Integration accuracy

- Outlook and LinkedIn now render as deferred, not certified for customer use, with no connection/setup actions.
- Salesforce and HubSpot retain “Automatic inbound · Manual outbound”.
- Slack remains an outbound notification destination.
- Gmail remains automatic inbound email acquisition.
- OpenAI remains operator-managed and does not invite customer API-key entry.

### Bundle and interaction stability

- Removed the unused Three.js/React Three Fiber dependency path from the production frontend bundle.
- The intelligence network uses the existing accessible DOM representation with stage buttons and inspection content. This removes a heavy decorative effect while preserving the operational interaction.
- The resulting build has no Vite large-chunk warning.
- API errors now distinguish expired sessions (401) and permission denial (403) with non-sensitive user-facing text.

## Final frontend build

`npm run build` completed successfully:

| Asset | Raw | Gzip |
| --- | ---: | ---: |
| Main JS | 311.57 kB | 91.87 kB |
| CSS | 89.93 kB | 18.14 kB |

The final build emits two assets and no avoidable major-chunk warning. The small raw-JS increase in the main bundle is outweighed by removing the 917 kB lazy network chunk and its 247 kB gzip payload.

## Browser walkthrough

Actual browser tested: Chrome/Chromium through a headless Chromium DevTools session with exact viewport emulation.

Verified screens and flows included authentication/session state, dashboard, leads, lead-row inspection path, pipeline, analytics, integrations, Gmail/Salesforce/HubSpot/Slack card states, team and permissions, workspace settings, ICP configuration, export controls, manual review, and activity/audit presentation. The accessibility tree showed semantic headings, named buttons, labeled search/date fields, status text, table headers, and keyboard-capable controls.

Edge was not directly tested because the executable was unavailable and is explicitly unverified. Safari and Firefox were not tested.

OAuth provider redirects were not exercised against external providers in this phase; no external-provider load was generated. The local integration UI accurately represented the already-certified provider states and deferred unsupported providers.

## Responsive review

Exact runtime checks were completed in headless Chrome/Chromium using DevTools viewport emulation. At every size, `document.scrollingElement.scrollWidth <= innerWidth`; no horizontal page overflow was observed.

| Viewport | Runtime result |
| --- | --- |
| 1440 x 900 | Pass; no page overflow; desktop navigation, cards, forms, tables, and dialogs usable |
| 1024 x 768 | Pass; no page overflow; intermediate grids and independently scrollable panels usable |
| 768 x 1024 | Pass; no page overflow; compact navigation, cards, forms, and contained overflow usable |
| 430 x 932 | Pass; no page overflow; single-column controls and mobile navigation usable |
| 390 x 844 | Pass; no page overflow; compact controls, tables, dialogs, and mobile navigation usable |

Screens exercised across the requested sizes were login, dashboard, leads, lead detail, pipeline, analytics, integrations, team, permissions, ICP configuration, activity/audit, workspace settings, and export controls. Lead detail was opened from a lead row. The mobile navigation drawer was opened and its primary links remained reachable.

The leads, team/members, and activity/audit tables use contained horizontal scrolling at small widths rather than excessively shrinking text. Export controls were checked as a dialog-based flow. Pipeline, network, charts, and ICP lifecycle strips retain intentional contained scrolling where applicable.

One genuine defect was found and fixed: the page-enter animation applied a transform to `.view-transition`, which changed the containing block for the fixed team invitation dialog after the page had been scrolled. The smallest safe fix removed only that transform from the enter animation. After the fix at 390 x 844 and `scrollY=1470`, the invitation dialog bounds were top 49 px and bottom 834 px within the 844 px viewport. The affected viewport was rerun successfully.

## Accessibility and motion

- Important interactions are semantic buttons, links, checkboxes, native selects/date controls, and labeled fields.
- Lead rows retain Enter/Space keyboard activation for inspection.
- Headings and table headers were present in the browser accessibility tree.
- Status indicators include text such as Verified, Deferred, Not Synced, and Manual Review; status does not depend only on color.
- Dialogs retain `role="dialog"`, `aria-modal="true"`, an accessible title reference, a visible close button, and usable focus; the invitation email input receives focus when opened.
- Existing `prefers-reduced-motion: reduce` rules remain in place and were confirmed through emulation. The page transition no longer transforms the view, and the heavy 3D network path was removed from the rendered product view.
- No high-impact accessibility defect was found in the walkthrough. A full automated contrast scan was not available in the current browser surface.

## Backend performance and query findings

Measurements were made against the local current backend on 2026-09-26 using the demo workspace header, 10 sequential requests per endpoint, warm local Postgres/Redis, and no external providers:

| Operation | Median | P95 | Statuses |
| --- | ---: | ---: | --- |
| `/health/live` | 4.9 ms | 12.4 ms | all 200 |
| `/health/ready` | 16.2 ms | 22.4 ms | all 200 |
| lead list, limit 50 | 35.6 ms | 44.2 ms | all 200 |
| filtered lead list | 17.4 ms | 20.3 ms | all 200 |
| lead detail | 33.9 ms | 37.6 ms | all 200 |
| dashboard | 55.3 ms | 73.7 ms | all 200 |
| analytics channels | 74.5 ms | 91.4 ms | all 200 |
| activity | 26.0 ms | 27.2 ms | all 200 |
| integrations | 21.1 ms | 22.1 ms | all 200 |
| team | 18.5 ms | 22.1 ms | all 200 |
| ICP | 17.1 ms | 18.3 ms | all 200 |

PostgreSQL `EXPLAIN (ANALYZE, BUFFERS)` for the workspace lead ordering query used `ix_leads_workspace_received`, returned 44 rows, touched 23 shared buffers, and completed in 0.346 ms on the local dataset.

The due-work scheduler query is bounded to at most 500 rows, orders by `next_sync_at`, uses `FOR UPDATE SKIP LOCKED` when claiming work, and retains Phase 7D lease/idempotency behavior. Existing migrations already provide the due-time and lease-expiry indexes; no duplicate index was added.

## Controlled load test

The load test used only local internal reads. Twenty lead-list requests were issued at concurrency levels 1, 4, and 8. Each request used a fresh local HTTP client, so the timings include connection/client setup and are intentionally not presented as steady-state capacity claims:

| Concurrency | Median | P95 | Errors |
| ---: | ---: | ---: | ---: |
| 1 | 693 ms | 872 ms | 0/20 |
| 4 | 1,274 ms | 1,686 ms | 0/20 |
| 8 | 2,397 ms | 2,706 ms | 0/20 |

These results are a local development smoke-load signal, not a production capacity benchmark. The endpoint remained error-free; connection setup dominated the measured concurrent timings. No external provider was called repeatedly.

## Loading, errors, auth, and network behavior

- Dashboard and leads show intentional initial loading states.
- Lead search cancels stale requests and does not replace existing data with a blank screen during normal background refresh.
- Lead failures show a retryable alert.
- 401 and 403 responses now produce explicit session-expired and permission-denied messaging.
- The frontend does not add an automatic retry loop.
- Query requests are deduplicated by the existing GET in-flight map, and lead searches use abort signals.
- Production auth safeguards remain covered by the backend regression suite: secure cookies, CSRF validation, session rotation/revocation, membership checks, and production CORS validation.

## Replit-readiness review

No deployment was performed. The remaining work is configuration and hosting work for Phase 7F:

- replace local `VITE_API_PROXY_TARGET`/Vite proxy usage with the deployed frontend API base and exact production CORS origins;
- provide managed Postgres/Redis URLs and durable storage/secrets through Replit configuration;
- set public HTTPS frontend/API/integration base URLs and register exact OAuth callback URLs;
- enable secure production cookies and production authentication settings;
- confirm process startup, worker, and beat commands in the Replit run configuration;
- validate static asset serving and same-site/cross-site cookie behavior at the deployed domains.

The source contains local defaults and PowerShell-oriented README instructions for development, but production validation rejects localhost/private callback hosts, wildcard credentialed CORS, insecure cookies, and development auth. No clear portability blocker was found in the application code.

## Regression checks

- Full backend suite: **284 passed, 4 warnings**.
- Targeted Phase 7E/regression set: **71 passed, 2 warnings**.
- Ruff: passed.
- Strict mypy: passed for 86 source files.
- Frontend production build: passed, no large-chunk warning.
- `git diff --check`: passed; only normal Windows LF/CRLF conversion warnings were reported by Git.
- Alembic current/head: `0019_unique_lead_source_event (head)`.

No migration was added in Phase 7E.

PHASE 7E PERFORMANCE/UX CERTIFICATION: PASS
