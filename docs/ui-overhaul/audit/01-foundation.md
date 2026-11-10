# Audit 01: design foundation and CSS architecture

Branch `ui/overhaul` (from `fix/agent-truthfulness-security-speed`, `4231b24`). Read-only. All counts come from scripts over `frontend/src/**/*.css` with comments stripped, unless a line reference is given. Build output is from `npm run build` on this branch.

## Headline

| Metric | Value |
|---|---|
| CSS files | 51 (18,688 lines) |
| Built CSS | 753.3 kB, 125.6 kB gzip, in one `index-*.css` |
| UI generations running at once | 2: legacy (cascade layer `legacy`) and v3 (unlayered). v3 has its own token set, primitives and shell. |
| Custom properties | 182 unique names, 322 definitions; 43 names defined in more than one file; 23 unused |
| Raw colours outside token definitions | 1,756 uses, 858 distinct values (1,322 in `styles.css`) |
| Distinct font sizes | 190 |
| Distinct spacing px values | 84 (only 42.4% on a 4px grid) |
| Distinct radii | 61 |
| Distinct box-shadows | 242 |
| Distinct z-index values | 33 (from -2 to 10000) |
| Distinct durations | 55 |
| `!important` | 266 |
| `backdrop-filter` | 39 |
| `outline: none/0` | 19 sites |
| Selectors defined in more than one file | 496 (27 in four or more files) |
| CSS class names | 1,973 distinct |
| `prefers-color-scheme` | 0 (dark mode is manual only) |

## 1. Import and cascade map

Load order is set by `src/main.tsx:4-15`:

| Order | File | Lines | Mechanism | Role |
|---|---|---|---|---|
| 1 | `design/legacy.css` | 43 | `main.tsx:4` | Wrapper. `@import`s 33 files into `layer(legacy)`, listed below. Comment at `legacy.css:1-7`: "Unlayered v2 rules win over anything in this layer… Legacy !important declarations still win". |
| 1a | `designTokens.css` | 89 | legacy.css:8 | Legacy tokens (`--orange`, `--blue`, `--brand-*`, `--space-*`, `--control-height: 44px`, Segoe fonts) |
| 1b | `styles.css` | 3,608 | legacy.css:9 | Original app stylesheet: 657 classes, 201 dark selectors, 245 gradients |
| 1c-1ee | the 31 component and view sheets listed below | | legacy.css:11-43 | Component styles plus patch sheets |
| 2 | `v3/foundation.css` | 227 | main.tsx:5 | v3 tokens (`--tint`, `--label*`, `--r-*`, `--s*`, `--t-*`, `--z-*`, `--d-*`), Inter import, base element styles |
| 3 | `v3/components.css` | 455 | main.tsx:6 | v3 primitives (`.v-btn`, `.v-badge`, `.v-menu`, `.v-sheet`, `.v-field`) |
| 4 | `v3/shell.css` | 395 | main.tsx:7 | v3 sidebar, toolbar and tab bar |
| 5 | `v3/screens.css` | 427 | main.tsx:8 | v3 screens: Leads, LeadRecord, Pipeline, Today |
| 6 | `v3/compat.css` | 238 | main.tsx:9 | **Patch.** Restyles legacy screens to look like v3. 65 `!important`, the most of any file. |
| 7 | `v3/auth.css` | 78 | main.tsx:10 | Sign-in |
| 8 | `v3/v4.css` | 1,205 | main.tsx:11 | **Patch and next generation.** A "v4" pass layered on v3: 31 `!important`, 8 blurs. |
| 9 | `v3/polish.css` | 724 | main.tsx:12 | **Patch.** 53 `!important`; redefines `:root`, `input`, `select`, `textarea`, `.button-quiet`. |
| 10 | `v3/audit.css` | 247 | main.tsx:13 | **Patch.** Fixes from an earlier audit pass; 6 `!important`. |
| 11 | `components/corralAgentMobile.css` | 1,037 | main.tsx:15, after `App` | **Patch.** Mobile overrides for the agent dock, loaded after everything else. |

Sheets pulled into `layer(legacy)` by legacy.css, in order: ActivityToasts (12), LeadNotificationCenter (23), emailWorkspace (183), leadAssistant (457), corralAgentDock (2,223), workspaceSwitcher (23), DialerPhoneDock (461), pinnedLeadNavigation (13), leadOutcomes (24), leadDetailTabs (114), dashboardSignals (113), dashboardInsights (126), analyticsInsights (139), teamPerformance (33), PipelineView (70), workspaceBusinessProfile (76), navigationMenus (309), **usabilityFixes (75, patch)**, navigationLayout (92), **leadDetailPolish (401, patch)**, subpages (212), leadBrowserLayout (73), tableTypography (124), brandButtons (80), integrationExpansion (33), operations (421), **layoutConsistency (1,504, patch: 314 classes, redefines `:root`, inputs and buttons)**, OpenAIUsagePanel (546), SalesforceSetupGuide (410), RecordsView (134), DialerView (400).

Also imported directly from TSX, outside the layer:
- `DialerContactDirectory.css` (`DialerContactDirectory.tsx:5`)
- `assistantNoteDraft.css` (`AssistantNoteDraft.tsx:2`)
- `ProductTour.css` (`ProductTour.tsx:3`)
- `providerEmailBody.css` (`ProviderEmailBody.tsx:2`)
- `assistantLeadDraft.css` (`AssistantLeadDraft.tsx:3`)
- `calendarWorkspace.css` (`CalendarWorkspace.tsx:5`)

Five files are imported both into the legacy layer and from TSX: `emailWorkspace.css` (legacy.css:13 plus `WorkspaceAssistantChat.tsx:35` and `LeadAssistantPanel.tsx:28`), `leadAssistant.css`, `leadOutcomes.css`, `leadDetailTabs.css` and `PipelineView.css`. Vite dedupes them (for example `.lead-outcome-receipt{` appears once in dist), so the double import has no effect. It is still misleading about which layer actually applies.

`views/ContactsView.css` (77 lines) is not imported anywhere, so it is dead.

### Which screens use which generation (App.tsx:1461-1720)

- **v3 screens:** `LeadsScreen`, `PipelineBoard`, `RevenueScreen`, `PerformanceScreen`, `ReviewScreen`, `TodayScreen`, and the v3 `Sidebar`/`TabBar`/`Toolbar`/`PhonePanel`/`CommandBar` (App.tsx:115-130).
- **Legacy views restyled by compat/v4/polish:** DashboardView, AgentsView, AnalyticsView, RecordsView, PoliciesView, ComplianceView, IntelligenceView, AutomationsView, IntegrationsView, EmailWorkspace, CalendarWorkspace, DialerView, SettingsView, TeamView, IcpView, and LeadDetail with its panels.

This split is the root cause of the visual inconsistency: two component systems, joined by roughly 3,900 lines of patch CSS (compat, v4, polish, audit, layoutConsistency, usabilityFixes, leadDetailPolish, corralAgentMobile).

## 2. Tokens

There are two complete token systems:
- **Legacy**, in `designTokens.css`: `--bg`, `--surface`, `--text`, `--muted`, `--orange*`, `--blue*`, `--space-*`, `--radius*`, `--control-height: 44px`, `--font-body: 16px`.
- **v3**, in `foundation.css:18-130`: `--canvas`, `--surface`, `--label`, `--label-2/3/4`, `--tint*`, `--attention*`, `--danger*`, `--r-xs..xl`, `--s1..s16`, `--t-*`, `--d-*`, `--ease-*`, `--control-h: 32px`.
- Each has its own `html[data-theme="dark"]` block (`designTokens.css:64`, `foundation.css:137`).
- `:root` is redefined in 6 files: compat, designTokens, foundation, layoutConsistency, polish, v4.

Misnamed tokens (`designTokens.css`):
- `--brand-orange` (line 19), `--orange` (24), `--orange-hot` (25) and `--orange-soft` (26) all hold green `#1e6b52`.
- `--blue` (30), `--blue-mid` (32), `--blue-soft` (33) and `--blue-line` (34) also hold green; `--blue-deep` (31) is navy.
- In dark mode `--orange` is `#6bc39a` (light green, line 77).
- The legacy names are still referenced 291 times (`var(--orange|--blue|--brand-orange…)`), against 480 references to the v3 `--tint`/`--label*`.

Conflicting control height: legacy `--control-height: 44px` against v3 `--control-h: 32px` (`components.css:74`, `height: var(--control-h)`). A 32px control is below the 44px touch target on touch devices.

The 23 unused tokens include the entire v3 spacing scale. `--s1, --s2, --s3, --s4, --s5, --s6, --s8, --s10, --s12, --s16` are defined at `foundation.css:96` and never referenced, so v3 defined a 4pt grid and nothing uses it. The rest: `--brand-navy, --c-navy, --ease-in-out, --elev-drag, --ink-night, --material, --navy, --navy-high, --paper, --shadow-control, --space-8, --z-sheet, --z-toast`.

### Raw colours

1,756 uses (858 distinct values) outside custom-property definitions.

| File | Raw colour uses |
|---|---|
| styles.css | 1,322 |
| calendarWorkspace.css | 76 |
| DialerPhoneDock.css | 54 |
| SalesforceSetupGuide.css | 53 |
| corralAgentDock.css | 48 |
| v3/components.css | 22 |
| ProductTour.css | 19 |
| leadAssistant.css | 19 |
| teamPerformance.css | 16 |
| analyticsInsights.css | 15 |
| v3/screens.css | 13 |
| layoutConsistency.css | 12 |
| v4.css | 10 |

Most common values:
- `#fff` (163) and `#1e6b52` (79, the brand green hard-coded instead of `var(--tint)`)
- Near-duplicate greens `#22634f` (21), `#216c52` (13) and `#155640` (12)
- About 15 different alpha steps of the brand green, from `rgba(30,107,82,.055)` to `.35`
- `#0f1b2d` (14) and `#f5f7f4` (14)

There are also 337 raw colours inside `html[data-theme="dark"]` rules.

## 3. Typography, spacing, shape, motion

**Font families.** There are 67 declarations. The token stacks disagree:
- v3 `--font` is `-apple-system, "SF Pro Text", "Inter Variable", …` (`foundation.css:21`).
- Legacy `:root` is `"Segoe UI Variable", "Segoe UI", system-ui` (`designTokens.css:4`).
- Legacy `--font-display` is `"Arial Nova", "Segoe UI Variable"…`, used 20 times.
- `--font-mono` is `"Cascadia Mono"…` in legacy and `ui-monospace, "SF Mono"…` in v3.

Stray stacks: `Arial, sans-serif` (2), and `var(--font) !important` (1).

**Font sizes.** There are 190 distinct values. Most use raw px: 13px (571), 12px (152), 14px (99), 11px (95), 16px (93), 10px (67), 15px (40), 18px (24), 20px (23), 17px (13), 9px (11), 28px (11), 22px (9), 19px (9), 25px (8), 21px (8).
- Values of 9-11px appear 173 times, which is below readable body size.
- The v3 type tokens are used much less: `--t-footnote` (63), `--t-callout` (57), `--t-subhead` (24), `--t-headline` (15), `--t-title3` (11).
- There are 36 instances of `font: 13px var(--font-mono)`.

**Line heights.** 26 distinct values: 1.5 (142), 1.4 (59), 1.45 (55), 1.6 (53), 1.55 (42), 1.35 (37), 1 (33), 1.3 (31), 1.2 (17), and more, plus px values of 20px, 38px and 22px.

**Font weights.** 14 distinct values: 700 (91), 600 (71), 650 (65), 500 (33), 400 (27), 750 (25), 800 (18), and one-offs of 550, 450, 850, 720, 480, 530 and 520.

**Spacing.** 84 distinct px values in margin/padding/gap/inset: 12px (522), 8px (511), **10px (453)**, **14px (320)**, 16px (270), 4px (245), **6px (237)**, **18px (187)**, **9px (185)**, **7px (161)**, **5px (151)**, **3px (150)**, 1px (150), 20px (144), 2px (131), **11px (103)**, 24px (99), **13px (82)**, **15px (61)**, **22px (56)**, **17px (29)** and more. Only 42.4% of declarations fall on the 4px grid (values in bold are off it).

**Radii.** 61 distinct values: 50% (94), 8px (89), 10px (84), 9px (83), `var(--r-sm)` (41), `var(--r-md)` (41), 7px (38), 12px (37), `var(--r-pill)` (35), 999px (33), 11px (31), `var(--radius-sm)` (30), 6px (22), 5px (16), 14px (13), and more. Raw values outnumber both token systems.

**Shadows.** 242 distinct values. 70 are `none`, plus 10 `none !important`. Token use is low: `var(--elev-1)` 19, `var(--shadow)` 10, `var(--shadow-pop)` 6. Ad-hoc green glows include `0 8px 22px rgba(30,107,82,.17)`, `0 0 10px var(--green)`, and `text-shadow: 0 0 18px rgba(30,107,82,.25)` (`styles.css:1443`).

**z-index.** 33 distinct values, including 1, 2, -1, 0, 4, 5, 620, 619, 90, 80, 40, 20, 100, 2500, 1050, 120, 10000, 2000, 60, 1000, 19, 15 and 14. Tokens (`--z-pop`, `--z-inspector`, `--z-sidebar`, `--z-toolbar`) are used only 7 times, and `--z-sheet` and `--z-toast` are defined but unused.

**Motion.** 55 distinct durations: 160ms (40), 140ms (23), 180ms (19), 220ms (14), .01ms (11, reduced motion), 260ms, 240ms, 320ms, 45ms, 120ms, 420ms, 300ms, 100ms, 1.8s, 1.6s, 90ms, 135ms, 150ms, 1.5s and others.
- Easing is plain `ease` 188 times, `ease-out` 46, and `cubic-bezier(.2,.8,.2,1)` 11.
- The v3 easing tokens are barely used: `--ease-out` (`cubic-bezier(.22,1,.36,1)`) 4 times, and the legacy `--ease` 2 times.

**`backdrop-filter` (39 uses).** styles.css 9, v4 8, compat 6, screens 4, shell 4, layoutConsistency 2, components 2, polish 2, SalesforceSetupGuide 1, operations 1.

**`!important` (266 uses).** compat 65, polish 53, styles 32, v4 31, screens 26, corralAgentDock 9, corralAgentMobile 6, audit 6, foundation 6, operations 5, DialerView 5, DialerPhoneDock 4, ProductTour 4, layoutConsistency 3, OpenAIUsagePanel 2, leadAssistant 2, leadDetailTabs 2, others 1 each.

**Breakpoints.** There are 47 distinct media queries. The 760px (39) and 767px (19) breakpoints coexist, alongside 360, 420, 430, 480, 520, 560, 600, 640, 650, 700, 900, 960, 1000, 1024, 1100 and 1280.

## 4. Duplicate primitives

| Primitive | Implementations (evidence) |
|---|---|
| Button | 34 CSS class families. The main ones are legacy `.primary-button`, `.secondary-button`, `.button-quiet`, `.danger-button` and `.icon-button`; v3 `.v-btn`, `.v-iconbtn` and `.v-filterbtn`; the tour's `.ct-tour-button*`; and the workspace chat's `.cw-button*`. Plus `.oauth-button`, `.google-auth-button`, `.records-*-button`, `.lmp-link-button`, `.t-compare-btn` and `.v-fact-btn`. TSX has 488 raw `<button>` against 67 `<Button>` and 12 `<IconButton>` (v3 `primitives.tsx:17,47`). Class usage in TSX: button-quiet 122, secondary-button 73, primary-button 56, lmp-link-button 11. `.button-quiet` is defined in 5 files (audit, brandButtons, layoutConsistency, polish, styles). |
| Select | Three approaches. Native `<select>` appears in at least 12 files (Charts.tsx 9, IntegrationsView 7, ComplianceView 5, AutomationsView 5, LeadsScreen 5, DealRoom 5, ContactsView 4, LeadDetail 4, LeadRecord 3, LeadAssistantPanel 3, CallsPanel 3, TeamView 2). The custom `SelectMenu` is used in 4 files. `NativeSelectEnhancer.tsx` is mounted globally from App.tsx. The `select` element selector is restyled in 10 files. |
| Dialog / sheet / drawer | 33 classes, including `confirm-modal`, `team-modal*`, `team-member-drawer*`, `lead-drawer*`, `export-dialog`, `icp-publish-modal`, `record-editor-dialog`, `reasoning-backfill-modal`, `slack-connection-dialog`, `automation-enrollment-dialog`, `call-history-action-dialog`, `ops-dialog`, `cw-dialog`, `ct-tour-dialog`, `salesforce-guide-backdrop` and v3 `v-sheet*`. In TSX: native `<dialog>` in OperationsDialog.tsx, WorkspaceSwitcher.tsx and primitives.tsx; `<Sheet>` used in 6 files; `OperationsDialog` in 11; focus-trap helpers in 3; `role="dialog"` in 4. |
| Card / panel | 86 classes, for example `panel`, `data-panel`, `p-panel`, `a-card`, `b-card*`, `metric-card`, `insight-card`, `signal-card`, `records-card*`, `provider-card`, `integration-card`, `settings-card`, `review-card*`, `ops-panel`, `analytics-panel*`, `calls-panel*` and `dialer-panel*`. v3 `Group` (`primitives.tsx:385`) is the intended one. |
| Badge / pill / chip / tag | 28 classes: `badge`, `v-badge`, `b-pill`, `b-stage-pill`, `v-stage-chip`, `cx-chip`, `d-chip`, `t-chip`, `ops-tag`, `source-tag`, `qualification-badge`, `mode-badge`, `weight-pill`, `you-badge`, `read-only-badge`, the `gmail-*-badge` classes, `sync-result-badge-*`, and BEM `assistant-*-draft__badge`. |
| Table | 25 classes: `lead-table`, `v-lead-table`, `v-table`, `t-table`, `ops-table`, `rep-table`, `channel-table`, `analytics-data-table`, `call-history-table`, `performance-comparison-table`, `q-review-table`, `reasoning-provider-table`, `routing-capacity-table`, `lead-subpage-table` and others. `tableTypography.css` patches them. |
| Empty state | 39 classes, for example `empty-state`, `v-empty`, `ops-empty`, `cw-empty`, `k-empty`, `chart-empty`, `insight-empty`, `contacts-empty`, `dialer-empty`, `email-empty-state`, `lead-email-empty-state`, `provider-empty-state`, `review-empty-card` and `workspace-empty`. v3 `EmptyState` (`primitives.tsx:437`) is the intended one. |
| Skeleton / loader | 21 classes: `skeleton-content`, `skeleton-hero`, `skeleton-metrics`, `loading-state`, `loading-core`, `content-loading`, `inline-loading`, `is-loading`, `calls-loading`, `icp-loading`, `account-profile-loading`, `lead-history-loading`, `network-loading`, `usage-skeleton*`, `team-*-skeleton`, `gmail-sync-spinner` and `reasoning-usage-loading`. |
| Tabs / segmented | 16 classes: `auth-tabs`, `dialer-tabs`, `lead-detail-tab(s)`, `p-tabs`, `records-view-tabs`, `settings-category-tabs`, `v-section-tabs`, `v-tabs`/`v-tab*`. v3 `Segmented` is at `primitives.tsx:63`. |
| Toast / banner / alert | 32 classes: `activity-toast*`, `team-toast`, `notice`, `notice-*`, `cw-alert`, `cw-notice`, `team-alert`, `icp-notice`, `calls-notice*`, `v-banner`, `v-alert-bar`, `meeting-management-*` and `gmail-sync-active-banner`. |
| Avatar | 8: `b-avatar`, `contact-avatar`, `gmail-inquiry-avatar`, `member-avatar`, `records-avatar`, `rep-avatar`, `source-monogram` and `v-avatar`. v3 has `Monogram` at `primitives.tsx:448`. |
| Score | 11: `c-score`, `score-orb`, `score-contract`, `v-record-score*`, `v-score-cell`, `v-score-hero`, `v-score-sentence`, `v-score-story` and `v-fact-score*`. v3 has `ScoreRing` at `primitives.tsx:164`. |

## 5. Dark theme

- It is manual only: `html[data-theme="dark"]` is set from saved preference, with 0 `prefers-color-scheme` rules, so a first visit ignores the OS setting.
- Dark is defined twice, at `designTokens.css:64` and `foundation.css:137`.
- 201 dark selectors are in `styles.css` and 13 in `SalesforceSetupGuide.css`. Those rules contain 337 raw colours.
- **Gradients:** `styles.css` has 245. The dark canvas uses decorative radial gradients (`styles.css:1875`: `radial-gradient(ellipse 900px 520px at 18% 9%, rgba(30,107,82,.24)…)`). The dark sidebar uses a gradient plus a heavy shadow (`styles.css:1876`: `linear-gradient(180deg,#14231D,#101A19)`, `box-shadow: 14px 0 38px rgba(0,0,0,.25)`).
- Hard-coded light text colours appear only in dark rules, for example `#f0aaa5` (7), `#d5e9db`, `#e0eee3`, `#f2c37a` and `#a7d9bf`. They fail on light surfaces, so they break if a component renders outside its dark selector context.

## 6. Dead CSS candidates

Method: a class counts as a candidate if its name does not appear anywhere in `src/**/*.ts(x)` or `index.html`, and no `prefix-` fragment of it appears either (to allow for dynamically built names). This is conservative, so the real figure is higher.

- **Result:** 56 candidates out of 2,955 class-in-file pairs.
- **Top files:**
  - `styles.css` 14: `ambient-a`, `ambient-b`, `breadcrumb`, `capability-grid`, `demo-session`, `is-offline`, and others.
  - `v3/screens.css` 12: `v-board`, `v-boardcard*`, `v-column-empty`, `c-gen`.
  - `layoutConsistency.css` 6: `breadcrumb`, `demo-session`, `nav-expanded`, `nav-group`, `nav-separator`, `signout-button`.
  - `v3/compat.css` 6: `cx-chip`, `cx-op`, `v-boardcard`, `v-tile`.
  - `v3/polish.css` 4.
  - `v3/components.css` 3: `v-input`, `v-kbd`.
- **Whole dead file:** `views/ContactsView.css` (77 lines).
- **Bigger dead weight:** the selector-level overlap. 496 selectors are defined in more than one file, and 27 in four or more. For example, `select` is styled in 10 files, `input` in 9, `textarea` in 8, `:root` in 6, `a` in 5 and `.button-quiet` in 5. Most earlier declarations are overridden, so the effective dead CSS is far larger than 56 classes. Measure it with coverage in Phase 0 screenshots.

## 7. Accessibility in CSS

**`outline: none/0` (19 sites).** `LeadNotificationCenter.css:3,15`, `ProductTour.css:48`, `corralAgentDock.css:1056`, `layoutConsistency.css:897,1000`, `styles.css:5`, `v3/auth.css:60`, `v3/compat.css:133`, `v3/components.css:189,343`, `v3/foundation.css:210`, `v3/screens.css:13,203`, `v3/shell.css:366`, `v3/v4.css:218,715,811`, `views/emailWorkspace.css:101`. Each needs a check for a `:focus-visible` replacement. There are 108 `:focus-visible` rules in total, against 50 plain `:focus`.

**Reduced motion.**
- 19 `prefers-reduced-motion` blocks across 12 files.
- 15 files animate or transition with no reduced-motion block: DialerPhoneDock, OpenAIUsagePanel, corralAgentMobile, layoutConsistency, navigationLayout, subpages, workspaceBusinessProfile, v3/auth, v3/compat, v3/components, v3/polish, v3/screens, v3/shell, RecordsView and emailWorkspace.
- `foundation.css` has a global reduce block. Check that it covers these.

**Contrast risks** (WCAG, against white `#ffffff` and paper `#f5f7f4`):

| Colour | Contrast | Problem |
|---|---|---|
| `--label-4` `#8b949c` | 3.08 on white, 2.86 on paper | Used as text in 13 places: placeholders (`v3/auth.css:62`, `components.css:344`), disabled menu items (`components.css:193`), chevrons and markers. Fails 4.5:1 for placeholder text. |
| `--brand-coral` / amber `#f2a541` as text | 2.05 on white | Used at `styles.css:53, 859, 909, 1113, 1443`. Fails AA. |
| White on amber | 2.05 | Fails AA, so any filled amber badge needs dark text. v3 sets `--on-attention: #0f1b2d`, which is correct. |
| Legacy `--subtle` `#627480` | 4.50 on paper | Borderline pass. |
| v3 `--label-3` | 5.12 on paper | Pass. |
| `--tint` | 5.94 on paper | Pass. |

**Text size.** Text below 12px is used 173 times (9px 11, 10px 67, 11px 95).

**Touch targets.** v3 `--control-h: 32px` (`components.css:74`) is below 44px on touch devices. Legacy controls use 44px, so control sizes differ between screens of the two generations.
