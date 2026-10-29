# Audit 04: insight and admin surfaces

Scope: Today/Dashboard, Analytics, Revenue/Performance, AI Activity, Research & Coaching, Automations, Integrations, Team, Settings, ICP, Policies, Compliance, Agency, and their CSS.
Read-only audit on branch `ui/overhaul` (base `4231b24`). Paths are relative to `frontend/src/` unless noted. Line numbers were current at audit time.

## 0. Root cause: two UI generations render side by side

- `App.tsx:119-122` renders v3 screens for Today, Leads, Pipeline, Revenue, Performance and Review (`v3/screens/*`). The other views in scope (`views/AgentsView`, `AnalyticsView`, `IntelligenceView`, `AutomationsView`, `IntegrationsView`, `TeamView`, `SettingsView`, `IcpView`, `PoliciesView`, `ComplianceView`, `AgencyView`) are the legacy generation.
- The Dashboard mode of Today wraps the legacy `views/DashboardView` in a compatibility shell: `v3/screens/WorkScreens.tsx:145-149` (`<div className="v-compat">{dashboard}</div>`). Switching from Today to Dashboard therefore swaps design systems mid-page.
- There are two primitive systems:
  - v3 has `Button`, `IconButton`, `PageHeader`, `EmptyState`, `Banner`, `Segmented` and `Sheet` (`v3/ui/primitives.tsx:47,413,437,464`).
  - Legacy views hand-write class strings. Counts across the scope: `button-quiet` 64, `secondary-button` 35, `primary-button` 32, `danger-button` 2. `<Button` is used only 7 times.
- There are two chart libraries: `components/Charts.tsx` (585 lines: Bars, TrendChart, TimedAreaChart, Donut) and `v3/ui/charts.tsx` (154 lines: BarList, ColumnChart, GoalRing, Sparkline, SegmentBar).
- Page headers come in at least four hand-rolled variants, none of which use `PageHeader`:
  - `ops-page-head` (`views/AutomationsView.tsx:214`, `views/IntelligenceView.tsx:89`)
  - `page-hero` (`views/IntegrationsView.tsx:1146`)
  - a bare `<span class="eyebrow"><h1>` (`views/AgentsView.tsx:17`, `views/AnalyticsView.tsx:196`, `views/TeamView.tsx:258`)
  - `section-heading` sub-headers (`views/TeamView.tsx:277,300,303,442,450`)
- The same component is styled from many files:
  - `.integration-card` / `.provider-head`: 7 files (`styles.css`, `components/leadBrowserLayout.css` (unrelated to integrations), `integrationExpansion.css`, `layoutConsistency.css`, `v3/v4.css`, `v3/compat.css`, `v3/polish.css`)
  - `.ops-panel`: 4 files (`layoutConsistency.css`, `operations.css`, `v3/v4.css`, `v3/compat.css`)
- Fix: migrate every in-scope view onto one primitive set and one chart set, then delete the compat path.

## 1. The specific bugs requested

### "Booked" next to "Booked rate 100%", and percentages for a sample of one
- **Render:** `views/AnalyticsView.tsx:275-276` prints `row.booked_leads ?? 0` and `row.booked_rate ?? 0}%`. Every rate is printed whatever the sample size (`:271-277` for volume share, qualification rate, SQL rate, booked rate and won rate), with no "not enough data" threshold.
- **Backend 1:** `backend/src/lead_agent/application/analytics.py:261-269` computes `booked_rate = booked / len(items)`, with no minimum sample, so 1 lead gives 100%.
- **Backend 2:** cancelled meetings count as booked. The query at `analytics.py:220-232` selects `PipelineEventORM.stage == "meeting_scheduled"` with any status. A meeting that was later cancelled still counts. In production, the HR Tech lead's only meeting is cancelled (Ask Corral reported `status "cancelled"`), yet Email shows Booked 1 and Booked rate 100%.
  - Fix: filter on `status in {"meeting_scheduled","booked","confirmed"}` and exclude leads whose latest meeting event is cancelled.
- **No sample guard:** `qualification_rate`, `sql_conversion_rate` and `closed_won_conversion_rate` (`analytics.py:276-278`) also have none. `importance_index` (`analytics.py:259-260`) is an opaque weighted score with no explanation. It renders as a bare number in an `<em>` (`AnalyticsView.tsx:279`).
- **Fix:** return `null` below n=5 (or a configured threshold) along with the sample size, render "Not enough data (n=1)", and add an explainer tooltip for Importance.

### Blank Automations empty state
- `views/AutomationsView.tsx:306-311` renders a lone `<p className="ops-empty">` sentence. There is no icon, no primary action (the "New sequence" buttons sit far away in the header at `:222-238`), no starter templates and no example.
- "Operational trust" shows even with zero runs (`:247-255`). It prints the raw backend `trust.method` string ("Withheld below 5 runs…"), which is internal jargon.
- An empty "Run history" table renders below even when no definitions exist (`:312-331`), which duplicates the empty message.
- The FeatureGuide buttons are a detached row (`:246`).
- The eyebrow "Durable steps · human approval" (`:216`) is jargon.
- "Enroll leads" is disabled with no reason given when a definition is paused (`:283-291`).
- **Fix:** one `EmptyState` with 2–3 starter templates (for example "3-touch follow-up" or "Meeting no-show"), the primary CTA inside it, Run history hidden until a definition exists, the trust line shown only when n ≥ 5, and plain-language reasons on disabled buttons.

### Unlabeled icon buttons on the dashboard
- The Today KPI tiles are `<button className="h-kpi" onClick>` with no `aria-label` (`v3/screens/WorkScreens.tsx:304-305`). Their accessible name is the concatenated inner text with no separators, for example "Open pipeline$0.002 open deals". The production accessibility tree listed 6 nameless buttons in `main` (`ref_33-38`) plus 2 more (`ref_42-43`); these KPI tiles are the most likely source.
  - Fix: `aria-label={`${label}: ${value}. ${detail}`}` and keep the visual layout.
- The focus-list row actions are correctly labelled (`WorkScreens.tsx:217-219`). The legacy Dashboard mode (`views/DashboardView.tsx`) has 0 `<button>` elements; its interactive controls come from `Charts.tsx` `PlotSettings` (`components/Charts.tsx:184-246`), where the reset button (`:196`) is text-labelled.
- Not verified in a browser at audit time: re-check with the accessibility tree after Phase 1.

## 2. Today / Dashboard (`v3/screens/WorkScreens.tsx`, `views/DashboardView.tsx`, `components/DashboardInsights.tsx`, `DashboardSignals.tsx`)
- The KPI row mixes time bases, and the range control (Week / Month / Year) only partly applies:
  - "Open pipeline" is all-time open deals (`:160-161`).
  - "Won this month" is hard-coded to the calendar month (`:164-167`) whatever range is selected.
  - "New leads" shows `metric("total_leads")` (`:176-180`): the label says "new" but the metric is the total. It falls back to `views.length`.
- The accent (filled brand) tile highlights "Weighted forecast" even when its value is "—" (`:182`), so the strongest emphasis lands on the least informative metric.
- Currency is hard-coded to USD (`formatMoney(..., "USD")` at `:234`, `:302`; `v3/deals/dealStore.ts:654`), while analytics claims "currencies are kept separate". Multi-currency workspaces will see wrong symbols.
- Fraction digits are inconsistent:
  - `formatMoney` shows 2 decimals unless `compact` is set (dealStore.ts:658-660), which is why Today shows "$0.00".
  - `productShared.money` uses 0 decimals and a hard-coded `en-US` locale (`views/productShared.tsx:179-184`).
  - `v3/ui/primitives.money` uses 0 decimals and the browser locale (`:508-509`).
  - `TeamPerformancePanel.money` uses 2 decimals (`components/TeamPerformancePanel.tsx:4-6`).
  - `AdvancedAnalyticsPanel.amount` uses 0 decimals (`components/AdvancedAnalyticsPanel.tsx:3-10`).
  - `DashboardInsights` uses `undefined` locale with 2 decimals plus a `${currency} n` fallback (`components/DashboardInsights.tsx:18-20`).
  - That is six formatters for one concept. Replace them with one `format.ts` (money, percent, count, date, relative time).
- "Dashboard" mode subtitle copy: "Every metric and panel for the selected period. Choose which cards appear." (`WorkScreens.tsx:131`). Customisation is a details disclosure, which reads as admin tooling inside the home page.
- `window.confirm` is used for "Mark review done" (`WorkScreens.tsx:359`). This is a native blocking dialog, unstyled and inconsistent with the app dialogs. The same applies at `views/AutomationsView.tsx:405`, `views/SettingsView.tsx:1082` and `views/ComplianceView.tsx:451`.

## 3. Analytics (`views/AnalyticsView.tsx`, `components/AnalyticsInsights.tsx`, `AdvancedAnalyticsPanel.tsx`, `components/Charts.tsx`)
- The channel table is a CSS-grid of `<div>`/`<span>` (`AnalyticsView.tsx:254-283`), not a `<table>`. Screen readers get no header/cell relationships, and there is no sorting.
- The tab strip overflows horizontally at desktop width. In production the "Revenue by …" tab was clipped at 1512 px.
- "Revenue provenance: Unavailable" in amber (production) for a workspace with no revenue reads as an error. It should be a neutral empty state.
- Charts (`components/Charts.tsx`):
  - Palettes are hard-coded hex values that ignore theme tokens and dark mode: `:213` `["#1e6b52","#d79728","#457a9e","#8a98a3","#b8524b"]`, and `:245` trend colours `["#df8a4c",…]`. The trend palette starts with orange, not the brand green, so series colours differ between charts.
  - Every bar and point is `tabIndex={0}` (`:420`, `:422`, `:565`). A 30-point chart adds 30 tab stops, which makes keyboard navigation impractical. Use one focusable chart with arrow-key traversal.
  - The tooltip is `role="status"` (`:427`, `:567`), so screen readers re-announce it on every hover.
  - `aria-label="Time-series trend"` (`:407`) is generic; the label and summary should describe the data.
  - There are 9 inline `style={{…}}` uses in Charts.tsx.
- `AdvancedAnalyticsPanel` is appended under AnalyticsView in App.tsx (`App.tsx` ~1574-1576) as a separate block with its own loader and `amount()` formatter. It isn't integrated into the tab structure.

## 4. Revenue and Performance (`v3/screens/InsightScreens.tsx`, `components/TeamPerformancePanel.tsx`, `views/ProductViews.tsx`)
- `views/ProductViews.tsx` is a 6-line re-export shim (`AnalyticsBundle` type); remove it after migration.
- `TeamPerformancePanel` uses 2-decimal money and its own CSS (`components/teamPerformance.css`: 9 raw hex values, 13 px font sizes).
- InsightScreens has 3 inline styles. It is the v3 generation, so keep it as the reference pattern.

## 5. AI Activity (`views/AgentsView.tsx`, `components/AgentFlow.tsx`, `AiUsagePanel.tsx`, `OpenAIUsagePanel.tsx`)
- The eyebrow "Agentic operations center" (`AgentsView.tsx:17`) is marketing jargon inside a product page.
- `OpenAIUsagePanel` estimates cost from a hard-coded price table (`components/aiUsagePricing.ts:8-25`, "checked 2026-10-15"). Prices go stale silently. Label the figure "Estimate", show the "as of" date in the UI, and show "unknown" for unmapped models (the code already returns `null`; check that the UI says so).
- `OpenAIUsagePanel.css` is 546 lines with 11 px font sizes and 2 `!important`, for a single panel.

## 6. Research & Coaching (`views/IntelligenceView.tsx`)
- The legacy header (`:89-92`, eyebrow "Evidence and judgment") isn't on `PageHeader`.

## 7. Integrations (`views/IntegrationsView.tsx`, 1,575 lines, 88 KB chunk / 25 KB gzip, the largest lazy chunk)
- Disconnected or broken connections look nearly connected:
  - `connectionStage()` (`:153-158`) maps `connection_failed`, `authorization_expired`, `reconnect_required`, `expired`, `needs_reauthorization` and `error` to stage 2, "Credentials stored", which is 3 of 4 steps on the progress stepper (`:115`).
  - `connectionStatusLabel()` (`:160-165`) collapses all of those into "Needs authorization".
  - A failed or expired integration should render as an error state, never at 75% progress.
- Success is announced before verification: the OAuth callback toast says "{name} connected successfully." (`:627-628`, `:715-718`) while the card label for status `connected` is "Needs verification" (`:163`). That's a contradiction and violates the "verified, not assumed" rule.
- Provider identity: `provider-glyph` shows two-letter initials ("GM", "SA", "AI") instead of logos (`:1249`), which looks unpolished next to real brands. The expand toggle uses literal "+" and "−" characters (`:1249`).
- "Coming soon" providers render as full cards with a status badge (`:1173`, `:1187`, `:1276`), padding the page with unusable items. Collapse them into one "More providers" row or hide them.
- Permission-denied states expose raw permission codes: `<code>integrations.view</code>` (`:1168`), and the same in `views/TeamView.tsx:266` (`members.view`). Users should see a role-based sentence, for example "Ask an admin for access to integrations."
- 28 JSX lines over 300 characters (for example `:376` is one line holding the whole Slack routing-rule form, and `:1248-1249` hold the card header). These are unmaintainable and almost certainly hide layout inconsistencies. Split them into components.
- The integration-direction copy is a developer-voice ternary chain (`:1275`), with strings like "Application → Slack · outbound notifications only".
- Perf: the whole provider catalogue, setup guides and Slack routing ship in one chunk. Split per provider guide (SalesforceSetupGuide: 205 lines TSX + 410 lines CSS with 34 hex values; SlackSetupGuide).

## 8. Team / Users (`views/TeamView.tsx`)
- Copy:
  - "Delivery is represented as pending" (`:271`) is confusing.
  - "+ Invite user" uses a text plus where an icon belongs (`:277`).
  - "Durable administration trail" (`:303`) is jargon.
  - "{n} effective permissions" (`:272`) exposes an internal count.
- The summary cards use `team-summary panel` with eyebrows, which doesn't match the KPI tiles on Today (`h-kpi`). Use one KPI primitive.
- The roster table on mobile isn't verified. No stacked-card variant was found in TeamView.

## 9. Settings / ICP / Policies / Compliance / Agency (`views/SettingsView.tsx` 1,187 lines, `IcpView` 298, `PoliciesView` 599, `ComplianceView` 475, `AgencyView` 225)
- `window.confirm` is used for destructive actions in Settings (`:1082`) and Compliance (`:451`). Retention deletion is a high-stakes action and needs a designed confirmation dialog with consequences and typed confirmation.
- All are legacy-generation headers and forms. Field, label and help-text patterns differ per file; unify them on one Form primitive (label, help, error, required).
- `components/workspaceBusinessProfile.css` has 12 px font sizes; `components/operations.css` has 19 px font sizes and 5 `!important`.

## 10. CSS debt in scope (measured)

| file | lines | raw hex | !important | px font-size |
|---|---|---|---|---|
| components/SalesforceSetupGuide.css | 410 | 34 | 0 | 15 |
| components/OpenAIUsagePanel.css | 546 | 0 | 2 | 11 |
| components/operations.css | 421 | 0 | 5 | 19 |
| components/analyticsInsights.css | 139 | 15 | 0 | 0 |
| components/dashboardInsights.css | 126 | 0 | 0 | 10 |
| components/dashboardSignals.css | 113 | 9 | 0 | 0 |
| components/teamPerformance.css | 33 | 9 | 0 | 13 |
| components/workspaceBusinessProfile.css | 76 | 1 | 0 | 12 |

## 11. Remove or merge
- Delete `views/ProductViews.tsx` (a shim).
- Merge `components/Charts.tsx` into `v3/ui/charts.tsx`, or the other way round, keeping one.
- Retire the legacy `views/DashboardView.tsx` "Dashboard" mode, or rebuild it on v3 widgets, and drop `v-compat`.
- Collapse the "Coming soon" integration cards.
- Hide Automations Run history until a definition exists.
- Hide "Operational trust" below its sample threshold.
- Replace all four `window.confirm` calls with the shared dialog.
- Fold `AdvancedAnalyticsPanel` into the Analytics tabs.
- Collapse the six currency formatters into one.
