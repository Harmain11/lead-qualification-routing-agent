# Audit 02: Shell, navigation, global surfaces, data loading

Scope: `App.tsx`, `appNavigation.ts`, `main.tsx`, `permissions.ts`, `views/AuthView.tsx`, `v3/shell/*`, and the global components listed in the Phase 0 directive. Paths are relative to `frontend/`. Read-only audit at branch `ui/overhaul` (base `4231b24`).

Severity: **P0** = broken/untruthful/blocks enterprise sale, **P1** = clear UX or consistency defect, **P2** = polish.

---

## 1. Data loading and speed (App.tsx)

| # | Sev | Evidence | Finding |
|---|---|---|---|
| D1 | P0 | `src/App.tsx:648-682` | `refresh()` fires **11 requests in one `Promise.all`** (dashboard, channels, pipeline, revenue, reps, time-series, agent activity, manual review, extended, advanced, leads?limit=50). Every analytics-backed view waits for the slowest. Per view the real needs are: Today: dashboard, pipeline, review (+deal views). Pipeline: pipeline only (board uses `useWorkspaceLeads`). Revenue/Performance: revenue, reps, pipeline. Review: review. AI Activity: agents, pipeline. Analytics: channels, series, extended, revenue, reps, pipeline (+ `AdvancedAnalyticsPanel` fetches `advanced` again itself). Settings uses only `dashboard.metadata` (`App.tsx:1210`). |
| D2 | P0 | `src/App.tsx:836-868` | Polls `/v1/leads?limit=1` **every 5 s**, and re-runs the full 11-request `refresh()` **every 30 s** (`:852`) and on every new-lead signature change, on focus, on `online`, and after every call ends (`:877`). A user idling on any page pulls ~22 analytics requests per minute. |
| D3 | P0 | `src/v3/deals/useWorkspaceLeads.ts:6-7,31,53` | Pages through **all leads** (100/page, up to 600) on mount and again **every 20 s**. Combined with `App.tsx:680` (limit 50) and `App.tsx:841` (limit 1), `/v1/leads` is requested 3 different ways on boot. That is the "duplicate `/v1/leads`" seen in production. |
| D4 | P0 | `src/api.ts:260` | In-flight GET dedupe is **disabled whenever a `signal` is passed** (`key = method==="GET" && !options?.signal`). Almost every caller passes a signal, so no dedupe ever happens. |
| D5 | P1 | `src/App.tsx:328`, `:924` | `listIntegrations()` is called twice on every sign-in/workspace switch (dialer discovery and OpenAI-setup check), plus again by Integrations view. |
| D6 | P1 | `src/App.tsx:533`, `src/useAssignmentNotifications.ts:41` | Additional timers: deal records every 15 s, assignment notifications every 30 s. Four independent polling loops with no shared scheduler or backoff; none pause on `navigator.onLine === false`. |
| D7 | P1 | `src/v3/deals/useWorkspaceLeads.ts:7` | Hard cap `MAX_LEADS = 600`: Today, Pipeline, Revenue, Performance, quote picker and phone panel silently operate on the first 600 leads. Not enterprise-scale. Only Pipeline surfaces `truncated`. |
| D8 | P1 | `src/App.tsx:726` | `setLeads(nextLeads.slice(0, 1))` keeps **one** lead, but `leads` is passed to `CalendarWorkspace` (`App.tsx:1624`) where it fills the "Related lead" select (`views/CalendarWorkspace.tsx:218`). **Bug:** the calendar can only link events to a single lead. |
| D9 | P1 | `src/App.tsx:37-55,110-147` | Eager (in the 830 KB main chunk): AuthView, ProductViews (Dashboard/Analytics/Agents/Settings), LeadsScreen, Today/Review/Pipeline/Revenue/Performance screens, CorralAgentDock + WorkspaceAssistantChat (1,262 lines) + LeadAssistantPanel, ProductTour (490), DialerPhoneDock (506), PhonePanel, OpenAISetupPrompt, CommandPalette, NativeSelectEnhancer. Only 12 secondary views are `lazy()` (`:56-109`). All of the overlay components above can be lazy and mounted on first use. |
| D10 | P0 | `src/components/DialerPhoneDock.tsx:333-336` | The phone window (and its CloudTalk/RingCentral iframe) renders whenever a dialer is selected, even when closed (`is-closed`, `aria-hidden`). This is the ~7 s iframe on every page. Needs a deliberate decision: lazy-mount after idle, or keep warm only when the user opted into inbound calls. |
| D11 | P1 | `src/main.tsx:4-17` | 11 global stylesheets imported at boot; comment says legacy is layered and "v3 is unlayered and always wins". This is the cascade war behind 753 KB CSS. |
| D12 | P1 | `src/App.tsx:1442` | `key={workspace + view}` remounts the whole view on every navigation, discarding scroll, filters and fetched state. Combined with `window.scrollTo(top)` (`:1123-1125`) there is no scroll restoration on Back. |
| D13 | P2 | `src/App.tsx:1438` | When analytics are loading, analytics views show a generic 3-block skeleton (`ContentLoadingState`, `:1859`) that does not match any page layout (layout shift on arrival). |

## 2. Routing and deep links

| # | Sev | Evidence | Finding |
|---|---|---|---|
| R1 | P1 | `src/App.tsx:1111`, `:1135` | `range` is written into **every** URL (`&range=monthly` on Inbox, Calendar, Settings, etc.). Should only exist on views that use it. |
| R2 | P1 | `src/App.tsx:1678`, `:1684`, `:1727` | Team/ICP "Back" and OpenAI "Settings" call `setView(...)` directly instead of `navigate()`, so history entries, `section` and lead state are not updated consistently. |
| R3 | P1 | `src/appNavigation.ts:11-16` | `navigationSections.leads` still lists stale ids/labels ("AI & qualification", "Comments & closing", "History") that do not match the 8 lead tabs. |
| R4 | P2 | `src/appNavigation.ts:34,41` | Integrations sections define id `"ai"` twice ("AI connections" and "AI connection"). |
| R5 | P2 | `src/appNavigation.ts:30-31` vs `App.tsx:201-202` | Naming drift: "AI Research & Coaching" / "Workflows & Automations" vs sidebar "Research & Coaching" / "Automations". |
| R6 | P2 | `src/App.tsx:1565` | Review opens leads at section `"ai"`; the lead tab label is "Qualification" (`leadSections.ts:3`). Internal id leaks into URLs (`lead_tab=ai`). |
| R7 | P2 | `src/App.tsx:1074-1105` | Popstate rebuilds state but cannot restore filters, scroll or the open lead tab position. |

## 3. Shell: sidebar, toolbar, tab bar, footer

| # | Sev | Evidence | Finding |
|---|---|---|---|
| S1 | P1 | `src/v3/shell/Shell.tsx:145` and `src/App.tsx:1343` | Two search entry points on desktop (sidebar "Search" and toolbar `SearchField`), both open the same palette. Keep one. |
| S2 | P1 | `src/App.tsx:1183-1189`, `Shell.tsx:230-280` | Integrations sidebar item opens a **3-level hover flyout** (Integrations, Calling, CloudTalk…) with 22 entries. Hover-intent with a 450 ms close timer (`Shell.tsx:101-104`); `role="menu"` without Up/Down/Home/End handling. Keyboard and touch users get an inconsistent menu model. Move provider choice into the Integrations page. |
| S3 | P1 | `Shell.tsx:290`, `App.tsx:1285` | Account status shows "Connected" / "**Offline**" based on `!error`. Any failed request (e.g. a 409) labels the user "Offline". Use `navigator.onLine` plus a real connectivity signal. |
| S4 | P1 | `Shell.tsx:284-305` | Account menu lacks Profile/Account settings and Keyboard shortcuts; theme is a binary toggle that ignores `prefers-color-scheme` (`App.tsx:175-183`). No "System" option. |
| S5 | P1 | `App.tsx:1268` | The time-range control appears in the toolbar only for agents/analytics/review/team, while Today, Revenue and Performance place it inside the page (`:1471`, `:1556`, `:1562`). Same control in two locations. Mobile has a third placement (`:1425`). |
| S6 | P0 | `App.tsx:1696`, `views/AuthView.tsx:181`, `public/privacy.html:24,29,83`, `public/terms.html:24,29,68` | "Contact" is a personal Gmail mailto (`harmainmughal8@gmail.com`) in the app footer, sign-in footer and both legal pages. Not acceptable for an enterprise product. |
| S7 | P1 | `App.tsx:1698-1700` | Footer shows "Updated 3:27 PM · Workspace 74581acc" on every page: internal id and a dashboard-metadata timestamp unrelated to the current page. Remove from the footer; put the workspace id in Settings. |
| S8 | P1 | `App.tsx:1400-1411`, `src/v3/shell.css:303` | "Ask Corral" trigger is a bespoke `<button className="v-ask">` (not the `Button` primitive) with a PNG logo. On mobile the label is `display:none` and the only name is `title`. The dock itself is labelled "Corral Agent" (`CorralAgentDock.tsx:385`). Naming must be one thing. |
| S9 | P1 | `App.tsx:1349-1370`, `DialerPhoneDock.tsx:441-457`, `v3/shell/PhonePanel.tsx`, `views/DialerView.tsx` | Four phone entry points: toolbar "Phone", floating provider bubble ("CT"/"RC"/"A" text initials, `DialerPhoneDock.tsx:453-455`), PhonePanel sheet for direct dialers, and the Calls page. Consolidate to one phone surface. |
| S10 | P1 | `components/WorkspaceSwitcher.tsx:55-59` | Trigger shows four text items ("Workspaces", name, "Choose workspace", "Live workspace"). Chooser is a full-screen dialog with `h1` "Workspaces" and a "Back to Corral" button (`:68-69`); roles rendered as raw `role.replaceAll("_"," ")` lowercase (`:83`); "Illustrative" jargon. |
| S11 | P2 | `App.tsx:293`, `:1213` | Default accent `#1e6b52` hard-coded twice in TS instead of a token; luminance math duplicated inline (`:1238-1253`). |
| S12 | P2 | `index.html:6` | `theme-color` `#F5F7F4` does not match `--bg` `#f6f8fa`, and there is no dark `theme-color`, no `apple-touch-icon`, no web manifest, and no font preload. |

## 4. Auth (views/AuthView.tsx)

| # | Sev | Evidence | Finding |
|---|---|---|---|
| A1 | P0 | whole file | **No "Forgot password" flow.** Enterprise blocker. No show-password toggle and no caps-lock hint either. |
| A2 | P1 | `:134-136`, `:173` | Title Case buttons ("Sign In", "New Account", "Team Code", "Join Workspace", "Create Workspace", "Resend Link") next to sentence-case "Continue with Google", "Continue" and "Back". |
| A3 | P1 | `:173` | Busy label is always "Signing in…", also when creating an account or joining a workspace. |
| A4 | P1 | `:91` | The resend-verification **success** message is rendered with `a-error role="alert"` error styling (`:153`). |
| A5 | P1 | `:157` | Team Code mode requires typing a raw workspace UUID (`placeholder="xxxxxxxx-…"`). Use an invite link or short code. |
| A6 | P1 | `:132` | `role="tablist"` without `tabpanel`/`aria-controls` and without arrow-key navigation. |
| A7 | P2 | `:177` | Footer sentence "Sessions use a secure HttpOnly cookie; workspace access is checked on the server" is engineering copy on a sign-in page. |
| A8 | P2 | `:117-119` | Theme toggle is a text button "Dark"/"Light"; it ignores system preference. |
| A9 | P2 | `:110-114` | If Google redirect fails to start, `busy` stays true (no reset path). |

## 5. Overlays: dialogs, sheets, toasts, palette, tour, notifications

**Five separate dialog implementations** with different headers, close buttons and button styles:
1. `components/OperationsDialog.tsx` (native `<dialog>`, text button "Close ×" `:54`, `aria-label` instead of `aria-labelledby` `:40`), used by 10 files.
2. `v3/ui/primitives.tsx:346-354` `Sheet` (native `<dialog>`).
3. `components/WorkspaceSwitcher.tsx:60` (its own full-screen `<dialog>`).
4. `components/ProductTour.tsx:374` (custom `role="dialog"`, own SVG close `:391`, own `ct-tour-button` styles `:409-478`, hard-coded dimmer `rgba(12,25,21,.58)` `:369`).
5. `components/OpenAISetupPrompt.tsx:146-164` (borrows `team-modal-*` classes from Team view; `button-quiet` buttons).

| # | Sev | Evidence | Finding |
|---|---|---|---|
| O1 | P0 | `src/activity.ts:20-36`, `src/api.ts:308-311` | **Every successful non-GET request auto-toasts a message guessed from the URL**: `/sync` → "Synchronization completed successfully.", `/import` → "Import completed successfully.", any other → "Changes saved successfully." This is a truthfulness risk: the message is inferred from the URL, not from the action's actual result. It also duplicates in-context confirmations. Replace with explicit, action-owned feedback. |
| O2 | P1 | `components/ActivityToasts.tsx:11-15,27,29` | Max 3 toasts; **errors auto-dismiss after 5 s** like successes; no pause on hover/focus; glyph icons `✓ ! i ×` instead of the Icon set; no action (Undo/Retry/View). |
| O3 | P1 | `src/api.ts:287-305` | Raw backend `detail` strings are shown to users verbatim (e.g. "workspace membership is required", Pydantic `msg` joined by "; "). Needs a message map with human copy. |
| O4 | P1 | `components/CommandPalette.tsx:67,78,87` | **Dangling separator dots**: labels built as `` `${full_name} · ${company_name}` ``, `name + " · " + (email || "")`, `name + " · " + (domain || "")`. Missing company/email/domain renders "Name · ". |
| O5 | P1 | `CommandPalette.tsx:155-193` | Results are a flat list of `secondary-button`s (no grouping headers, no `role="listbox"/"option"`, no `aria-activedescendant`, no match highlighting, no recent items). Only pages and records: no actions (New lead, Ask Corral, Toggle theme, Go to settings section). Hidden pages (ICP, Routing, Consent) are not searchable. Wrapped in OperationsDialog with a "Search Corral" header and "Close ×", which is heavy for a palette. |
| O6 | P1 | `App.tsx:587-597`, `:1712` | First visit stacks two blocking modals: the 10-step ProductTour auto-opens, then the OpenAI setup modal appears as soon as the tour closes. |
| O7 | P1 | `App.tsx:573`, `:1720-1726` | OpenAI prompt dismissal is stored in a `useRef` Set (not persisted), so **the API-key modal reappears on every reload** for admins without a key. |
| O8 | P1 | `components/LeadNotificationCenter.tsx:78-80,109,146,171` | Inline SVG bell, `×` close and `›` chevrons instead of the Icon set. Header copy "{n} items need your attention · {n} unopened leads" has no pluralisation ("1 items"). Timestamps use `toLocaleString()` (`:143`) instead of the app's relative time. No "Mark all as read". Focus is not moved into the popover or restored. The ICP note repeats on every item (`:165-169`). Leads outside the last-50 snapshot show "New lead / Open to review this lead". |
| O9 | P2 | `components/FeatureGuide.tsx:4-61` | Five long manual-style guides in sheets, including engineering details ("operations worker… every 30 seconds", "trusted provider frame", env var names). Replace with short inline help and a docs link. |
| O10 | P1 | `components/DialerPhoneDock.tsx:474-480` | RingCentral setup tells end users to set `VITE_RINGCENTRAL_EMBED_CLIENT_ID` "before building". Developer instructions in product UI. |
| O11 | P1 | `components/DialerPhoneDock.tsx:335-337` | Closed phone window is `aria-hidden` but not `inert`, so its iframe remains in the tab order. |
| O12 | P2 | `components/focusTrap.ts:1-21` vs `ProductTour.tsx:166-193` vs native dialog | Three focus-trap mechanisms. |

## 6. Agent / AI action status rendering

Places that render AI or action status:
- `components/WorkspaceAssistantChat.tsx:840-1210`: workspace assistant action card (`<details className="corral-workspace-action">`). States: `actionStatus` undefined (proposed), `applying`, `sending`, `applied`, `dismissed` (`api.ts:178`), plus a free-text `actionError` and a `needsVerification` flag. **No explicit `failed`, `awaiting confirmation` or `uncertain` state.** All states render inside the same card and differ only by a line of text (`:1104-1121`). The card collapses after applied/dismissed (`:842`). Buttons change label per kind: "Approve and apply", "Save note", "Create lead", "Discard draft", "Not now" (`:1184-1208`).
- `WorkspaceAssistantChat.tsx:1101-1103`: "Review exact changes" shows **raw `payload_json` in a `<pre>`** to end users.
- `components/LeadAssistantPanel.tsx` (lead tab and dock lead scope): a separate implementation of the same concept (covered in audit 03).
- `src/activity.ts:20-36`: global toasts infer success from URLs (O1).
- `components/LeadNotificationCenter.tsx:131-133`: "Meeting reply needs review" (draft awaiting review) uses the same dot and style as "Lead assigned to you".
- `components/OpenAISetupPrompt.tsx:147`: background job status text only.
- `v3/deals/DealSyncStatus.tsx` (mounted at `App.tsx:1326`): deal sync banner.

Verdict: the five required states (explanation, proposed, editable draft, awaiting confirmation, completed) plus failed/uncertain are **not visually distinct and not shared**. Two assistant UIs plus toasts plus notifications each express status differently.

## 7. Dead code (safe-removal candidates, verified by import grep)

- `components/NavigationMenuItem.tsx` (331 lines): not imported anywhere.
- `components/NetworkDomFallback.tsx` and `components/networkData.ts`: only reference each other. Marketing copy ("Live intelligence topology", twinkle SVG).
- `components/PinnedLeadNavigation.tsx:58-82`: the `PinnedLeadNavigation` component is unused (only the `usePinnedLeadSections` hook is used; the sidebar renders pins itself in `Shell.tsx:200-225`).

## 8. Static pages

| # | Sev | Evidence | Finding |
|---|---|---|---|
| L1 | P0 | `public/privacy.html:28-30`, `public/terms.html` | Eyebrow "Early access · Testing draft" and a "Draft for testing… not legal advice" notice. Not sellable to enterprise as-is (needs real legal text, a company contact, a DPA/subprocessor reference). |
| L2 | P1 | `public/privacy.html:11-17` | Own inline palette (`--ink #162c44`, radial gradient) that differs from app tokens (`--text #0f1b2d`); light-only; 22 px radius not on the app scale. |

## Top 10 for the plan

1. Replace the 11-request boot `Promise.all` and the four polling loops with per-view queries, a shared cache, real in-flight dedupe, and a single scheduler (D1-D6).
2. Lazy-mount every overlay (agent dock, tour, OpenAI prompt, phone iframe, palette) (D9, D10).
3. Remove URL-guessed success toasts; introduce one shared, state-driven action card for all AI and outbound actions (O1, section 6).
4. Unify the five dialog systems and the toast, palette and notification styling on the primitives and Icon set (section 5).
5. Fix the Calendar one-lead bug and the 600-lead cap (D7, D8).
6. Fix the dangling separator dots in the palette and audit every `" · "` concatenation (O4).
7. Consolidate the four phone entry points and the two search entry points; simplify the Integrations flyout (S1, S2, S9).
8. Auth: add Forgot password, consistent casing, correct busy and success states (A1-A4).
9. Replace the personal Gmail contact and draft legal pages; remove the footer workspace id (S6, S7, L1).
10. Delete dead components (section 7).
