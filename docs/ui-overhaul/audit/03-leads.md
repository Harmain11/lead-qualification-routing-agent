# Audit 03 — Lead-centric surfaces

Read-only audit on branch `ui/overhaul`. Paths are relative to `frontend/src/` unless prefixed `backend/`.
"Unverified" means inferred from code, not observed in the browser.

## 0. Structural findings (affect every lead surface)

1. **Two parallel lead UIs; one is dead code.** `App.tsx:119-121,1481,1534` mounts the v3 screens (`v3/screens/LeadsScreen.tsx`, `LeadRecord.tsx`, `PipelineBoard.tsx`, `WorkScreens.tsx` ReviewScreen). These files are imported nowhere: `views/LeadsView.tsx` (1,313 lines), `views/PipelineView.tsx`, `views/ManualReviewView.tsx`, `views/ContactsView.tsx` (+ `ContactsView.css`), `views/AgencyView.tsx`, and `components/LeadDetail.tsx` (675 lines, only imported by the dead `LeadsView`). They can all be deleted after a build check. Their CSS (`leadDetailPolish.css`, `leadBrowserLayout.css`, `leadDetailTabs.css`, parts of `styles.css`) likely becomes dead too. Confirm with `grep` and screenshots before deleting.
2. **Two token systems in use at once.** The v3 layer uses `--label-2`, `--t-callout`, `--r-lg`, `--surface-sunken`, `--canvas`, `--fill`, `--elev-1` (`v3/foundation.css`, 118 custom properties). Legacy components use `designTokens.css` (`--muted`, `--radius-lg`, `--orange-soft`, `--surface-high`). Example: `components/LeadMeetingPanel.tsx:53-57` maps its own `--lmp-*` variables onto the legacy `--orange-hot`/`--orange-soft` tokens. This is the root cause of visual drift inside the lead page.
3. **Legacy components embedded in the v3 record through a compat shim.** `v-legacy-block` wraps legacy panels in 8 places (`v3/screens/LeadRecord.tsx:788,821` and others), restyled by `v3/compat.css:170-172`. Affected panels: LeadMeetingPanel, LeadAssistantPanel, EmailComposer, CallsPanel. Each brings its own buttons (`primary-button`), notices and spacing.
4. **CSS injected from component strings.** `components/LeadMeetingPanel.tsx:52+` defines `const styles = \`...\`` and renders `<style>{styles}</style>` at `:436`. One other component also renders a `<style>` tag. This bypasses the cascade order and can't be audited with the CSS tools.
5. **Native `window.confirm` dialogs (15 in scope), off-brand and not themeable:** `v3/screens/LeadsScreen.tsx:337,349`, `LeadRecord.tsx:192,212`, `WorkScreens.tsx:359`, `DealRoom.tsx:226`, `components/EmailComposer.tsx:224,390`, `LeadAssistantPanel.tsx:342,438,445`, `LeadMeetingManagement.tsx:48,74`, `views/EmailWorkspace.tsx:359`, `views/CalendarWorkspace.tsx:128`, `views/RecordsView.tsx:583`. Replace all of them with the shared confirm dialog, or undo where the action is safe.
6. **Monolithic screens.** `LeadRecord.tsx` is 1,641 lines with 13 effects; `LeadsScreen.tsx` is 1,022 lines with 9 effects. Inline `style={{}}` appears in `LeadsScreen` (7), `LeadRecord` (5, e.g. `:785` `marginTop: 18`), `DealRoom` (2) and `WorkScreens` (3).

## 1. Root causes of the confirmed live bugs

| # | Bug | Root cause | Fix direction |
|---|---|---|---|
| a | Email body merged into the "To" line; card says "No message text was included" | `gmailInquiry.ts:87-90` `normalizeHeaderBoundaries` splits a flattened inquiry ("From: … Subject: … To: x@y.com Hey, I want…") into header lines. `parseHeadersAndBody` (`:103-144`) then takes the body only after a blank line or a non-header line. The body therefore stays inside the `to` header value. There is a recovery for a body glued to `Date:` (`recoverBodyAppendedToDateHeader`, `:93-101`, applied at `:136-142`) but none for address headers (To/Cc/From). The empty body then hits `components/GmailInquiryView.tsx:163-165` ("No message text was included") while `Recipients` (`:134-147`) prints the whole sentence under "To". | After parsing address headers, cut the value after the last address token (`name <email>`, comma-separated list) and prepend the remainder to `body`. Add cases to `tests/gmail-inquiry.test.ts`. |
| b | Raw `&#39;` in inbox previews | `backend/src/lead_agent/infrastructure/gmail.py:1423` passes Gmail's `snippet` through unchanged. Gmail API snippets are HTML-escaped. `views/EmailWorkspace.tsx:379` renders it as text. The `&#39;` decoding in `gmailInquiry.ts:295` only applies to inquiry bodies, not to snippets. Same risk at `gmail.py:1483` (snippet used as a body fallback). | `html.unescape()` the snippet in the backend (one line, test it), or decode once in a shared frontend formatter. |
| c | Summary, banner and chat disagree about the meeting | Three independent sources. (1) The summary is LLM text frozen at qualification time: `v3/screens/LeadRecord.tsx:643` renders `analysis.summary.urgency`, never reconciled with later events. (2) The banner comes from `components/LeadMeetingPanel.tsx:24-43` `meetingIndicatorFor`, which maps only `offer_status`. Its `default` branch returns `appointment_needed` whenever `recommendations.detected`, so a **cancelled** meeting falls into "Appointment needed" (no `cancelled` case). It is lifted to the record via `onStatusChange` (`LeadRecord.tsx:134,174-180,789-796`). (3) The chat reads backend pipeline events, where `meeting_scheduled` has status `cancelled` (backend `lead_assistant.py` context and `_has_verified_meeting`). | One backend-derived `meeting_state` (none / requested / draft_ready / sent / booked / cancelled / needs_attention + timestamps) on the lead payload, consumed by banner, summary and chat. Label stale AI summary fields as "at qualification time" or drop meeting claims from the summary. |
| d | Score 100 next to a manual-review hold with no explanation | The score ring and band come from `qualification.total_score` (`LeadRecord.tsx:366-378`). The hold is `route.manual_review \|\| analysis.requires_manual_review` (`:172`). The hold reasons are only shown in the banner (`:478-489`), not where the score sits. The badge `BandBadge review={review}` swaps the band label for "Review" with no reason. Review rules (personal email domain) are independent of the score, and nothing in the UI says so. | Show "Score 100 · on hold: personal email domain" in one status summary, with a "Why" popover listing score factors and hold rules separately. |
| e | Primary button labelled "Human review required" | `components/EmailComposer.tsx:501-502`. When `sendBlockedReason` is set, the submit button's label is replaced with a status string. The button is disabled, but `.primary-button:disabled` only sets `opacity: .42` (`v3/compat.css:119`, `components/brandButtons.css:36-38`), so it still reads as a primary action. The same reason also shows as a notice at `:493`. | Keep the verb ("Send email"), disabled, and show the hold inline with a "Review lead" link to `LeadRecord`'s hold. |
| f | Last pipeline column clipped | `v3/v4.css:414-432`: `.b-board` is `display:flex; overflow-x:auto; padding:2px 2px 18px; margin:0 -2px`, and `.b-col` is `flex: 1 0 264px`. Seven stages need about 1,920px (264×7 + gaps), so the board scrolls. There's no end padding, scroll-padding or edge fade/scroll affordance, so the last column looks cut off rather than scrollable. Mobile sets `grid-auto-columns: 84vw` (`:565`) on a flex container, where it has no effect. Unverified whether the parent `.v-content-inner` also clips. | Fit-to-width at ≥1440px (`minmax(0,1fr)` grid), collapse empty stages to narrow rails, add `scroll-padding-inline`, an end spacer and an edge fade, and use a real snap carousel on mobile. |
| g | Drafts signed "Best, The team" | Hard-coded fallback sign-offs in the backend: `backend/src/lead_agent/application/email_templates.py:32,41`, `meeting_email.py:410,467`, `follow_up.py:281`, `infrastructure/gmail.py:2655`. They're used when no workspace signature or sender name is configured, and `apply_workspace_email_signature` doesn't replace an existing sign-off. | Sign with the assigned rep's display name (fallback: workspace name). If neither exists, omit the sign-off and show "Add your signature" inline in the composer. |

## 2. Per-surface findings

### Leads list — `v3/screens/LeadsScreen.tsx`
- **Triple lead fetching (perf).** On boot, `App.tsx:680` (`listLeads({limit:50})` inside `refresh`), `LeadsScreen.tsx:256` (`LEAD_PAGE_SIZE` 50, `:37`) and `v3/deals/useWorkspaceLeads.ts:31` all load leads. The last pages *sequentially* through every lead up to 600 (`PAGE=100`, `MAX_LEADS=600`, `:6-7`) and repeats every 20s (`:52-57`). This is the duplicate `/v1/leads` seen live, multiplied.
- **Background polling storm.** `App.tsx:836-872` polls `listLeads({limit:1})` every 5s and calls `refresh()` (all ~11 analytics requests) every 30s and on every tab focus. `App.tsx:533` polls deals every 15s. With ~1s server latency this keeps the network busy permanently. Replace it with one event/ETag-driven change feed or a longer, coalesced poll.
- **Table.** It's a semantic `<table>` with `scope="col"` (`:560-575`), which is good. It isn't virtualised (fine at 50 per page, but `useWorkspaceLeads` holds up to 600 rows in memory for the pipeline). There's no sticky first column for narrow widths (unverified on mobile: `v-lead-table` has only 2 rules in `v3/screens.css`).
- **Bulk delete and bulk CRM sync use native confirms** (`:337,349`). Bulk delete has no undo.
- 7 inline `style={{}}` uses. Move them to tokens.

### Lead record — `v3/screens/LeadRecord.tsx`
- **Header.** Copy number, Email, Quote, Ask Corral and an overflow menu. "Ask Corral" appears both in the header button and as the last tab. Merge them into one entry point.
- **Status is scattered.** Owner, value, stage and queue (`:340-365`), the score button (`:366-378`), the stage rail (`:381`), the review hold (`:478-500`), the meeting banner (`:506-545`) and the "In short" urgency (`:639-645`) are all separate cards. Consolidate them into one status summary (bug c/d).
- **All tab panels mount eagerly**, hidden with the `hidden` attribute (`:586,603,621,683,762,802,821`). Only `deal` and `calls` content is conditional (`:587,604`). Opening a lead therefore runs every panel's fetch effects (13 effects here, plus LeadMeetingPanel 4, LeadEmailPanel 3, LeadAssistantPanel 4). The meeting banner even depends on the hidden Follow-up panel mounting. Lazy-mount panels on first visit and move the meeting state to the lead payload.
- **Tab id/label mismatch.** Ids `ai` = "Qualification", `follow-ups`, `email`, `history` = "Activity", `agent` = "Ask Corral" (`leadSections.ts:1-10`). The URL `lead_tab=history` doesn't match the visible "Activity" label. Align ids with labels, keeping old ids as aliases in `parseLeadSection`.
- **Tabs** have the correct `tablist`/`tab`/`aria-selected` roles and roving tabindex (`:550-560`). Good; keep them.
- **Confirms** at `:192` (manual-review completion) and `:212` (delete).
- **Copy.** The banner says "Prepare an appointment draft, then review and send it from Emails" even though the draft is auto-prepared (product spec). Say "Draft ready in Emails" with a button that goes there.
- **Inline style.** `:785` `style={{ marginTop: 18 }}` is off the 4px grid.

### Follow up / meetings — `components/LeadMeetingPanel.tsx` (671 lines), `LeadMeetingManagement.tsx`, `MeetingOfferDrafts.tsx`
- Three components cover the same meeting lifecycle (detect, draft, offer slots, send, booked, reschedule/cancel) with their own styles (`LeadMeetingPanel` injects `<style>`, `:436`). Merge them into one "Meeting" card with the agent-state component.
- `meetingIndicatorFor` has no `cancelled` or `rescheduled` state (`:24-43`). Add both, sourced from the backend.
- Reschedule and cancel use native confirms (`LeadMeetingManagement.tsx:48,74`).
- `providerName` (`:20`) returns raw provider ids for unknown providers. Use a shared provider-label map (Integrations has its own; duplication).

### Emails — `components/LeadEmailPanel.tsx`, `EmailComposer.tsx`
- `EmailComposer` is 506 lines and is used in the lead Emails tab, the Inbox and Ask Corral. It uses legacy `primary-button`/`notice` classes inside the v3 shell (`:493,501`). It needs the v3 Button and Banner.
- Bug e (`:501-502`).
- **Editor.** It's a `contentEditable` with `role="textbox"` and `aria-multiline` (`:469-472`), which is good. It sets `innerHTML` from the draft/seed (`:140`). That's provider/AI-generated HTML (`html_body`). Confirm it is sanitised before injection; I didn't find a sanitiser in this file (unverified elsewhere). This is a security item.
- Discard and delete-draft use native confirms (`:224,390`).
- There's a "Compose with AI" guidance field above the editor. The AI result replaces the body with no diff or undo (unverified). Add undo.

### Ask Corral (lead) — `components/LeadAssistantPanel.tsx` (681 lines)
- Three native confirms for consequential actions (`:342` email send, `:438` CRM sync, `:445` delete). These should be the shared "Awaiting confirmation" state card, showing exactly what will happen.
- Assistant replies expose internal field names (seen live: `meeting_scheduled`, `cancellation_reason`). The backend reply should humanise them (backend, `lead_assistant.py`).
- The drafted email renders a full composer inside the chat transcript (seen live). It's heavy; use a compact draft card with "Open in composer".

### Draft cards — `AssistantLeadDraft.tsx`, `AssistantNoteDraft.tsx`
- Each has its own CSS file (`assistantLeadDraft.css`, `assistantNoteDraft.css`). Fold them into the five-state action card.

### Calls — `components/CallsPanel.tsx`, `CallInsights.tsx`, `CallReviewDetails.tsx`
- Small and coherent. Three components could be one `CallItem` with an expandable review. CallsPanel fetches on mount (3 effects); with eager panels this runs on every lead open (see above).

### Qualification — `components/AiStageAssessment.tsx`
- `:25` uses the same "Human review required…" phrase as the composer button and LeadDetail. Standardise review copy in one place ("On hold for review: <reason>").

### Inquiry viewer — `components/GmailInquiryView.tsx`, `gmailInquiry.ts`, `ProviderEmailBody.tsx`
- Bug a.
- Two near-identical empty states (`GmailInquiryView.tsx:165`, `ProviderEmailBody.tsx:20`) with separate CSS (`providerEmailBody.css`). Merge them.
- The quoted/forwarded and footer disclosures (`:166-176`) are good; keep them, restyled.

### Inbox — `views/EmailWorkspace.tsx` + `emailWorkspace.css`, `GmailAccountsPanel.tsx`
- Bug b (`:379`).
- **Privacy/UX.** It lists the entire mailbox (seen live: verification codes, login alerts). Add a "Lead conversations" default filter and make "All mail" explicit (scoping needs a small backend change).
- **Speed.** About 10s to first content live. "Loading connected Gmail accounts…" blocks the list until the account list resolves. Render the cached last mailbox immediately.
- Draft delete uses a native confirm (`:359`).
- The account selector is a plain field with no avatar or status (seen live). Unify it with WorkspaceSwitcher-style identity.

### Pipeline — `v3/screens/PipelineBoard.tsx`, `v3/v4.css:414+`
- Bug f.
- Drag and drop is implemented with `draggable` (`:188,267-269`). There's no `onKeyDown` on cards; a keyboard alternative appears to be `v3/deals/StageMoveSheet.tsx` (unverified that it's reachable from the card by keyboard). Verify this and add a visible "Move to…" menu.
- Column value shows `""` when there's no value (`:177`). Show nothing consistently, or "—", but match the KPI tiles.

### Review queue — `v3/screens/WorkScreens.tsx` ReviewScreen
- Completing a review uses a native confirm (`:359`). The copy is good ("clears the hold, applies routing, may notify the assignee"); keep it in a proper dialog.
- Seen live: it stayed on a skeleton for more than 4s. It depends on `data.review` from the monolithic `refresh` (`App.tsx` refresh). Fetch per view.

### Calendar — `views/CalendarWorkspace.tsx` (+ `calendarWorkspace.css`)
- Month grid only (seen live). Week and day views are missing; for a sales CRM, week view should be the default.
- Event delete uses a native confirm (`:128`). Past days are styled grey (seen live), but today isn't strongly marked. Unverified.

### Calls workspace / dialer — `views/DialerView.tsx` (687 lines, `DialerView.css`), `DirectDialer.tsx` (520), `DialerContactDirectory.tsx` (+css), dock in `components/DialerPhoneDock.tsx`
- Three dialer surfaces (the view, DirectDialer and the dock) plus `v3/shell/PhonePanel.tsx`, each with its own CSS (`DialerView.css` 400 lines, `DialerContactDirectory.css`, `DialerPhoneDock.css` 461). Consolidate them into one phone surface.
- The CloudTalk iframe loads on every page (about 7s live). Defer it to first use or idle, after verifying inbound-call needs.

### Contacts and Companies — `views/RecordsView.tsx` (865 lines, `RecordsView.css`), `AccountProfilePanel.tsx`
- `ContactsView.tsx` and `.css` are dead (see 0.1). `RecordsView` serves both modes (`App.tsx:68`, lazy). Good.
- The merge confirmation is a native confirm (`:583`). A merge is irreversible: use a dialog with a side-by-side preview.

### Indicators — `LeadRowIdentity.tsx`, `NewLeadDot.tsx`
- Small and fine. Make sure the new-lead dot has a text alternative ("New") for screen readers (unverified).

## 3. Elements to remove or merge (lead scope)

| What | Action |
|---|---|
| `views/LeadsView.tsx`, `views/PipelineView.tsx`, `views/ManualReviewView.tsx`, `views/ContactsView.tsx`(+css), `views/AgencyView.tsx`, `components/LeadDetail.tsx` | Delete (unreferenced). |
| "Ask Corral" header button and "Ask Corral" tab | Merge into one entry point. |
| `LeadMeetingPanel`, `LeadMeetingManagement`, `MeetingOfferDrafts` | Merge into one meeting card. |
| `AssistantLeadDraft`, `AssistantNoteDraft` and the composer-in-chat | Merge into the shared five-state action card. |
| Dialer surfaces (`DialerView`, `DirectDialer`, `DialerPhoneDock`, `PhonePanel`) | Merge into one phone component. |
| `GmailInquiryView` / `ProviderEmailBody` empty states and CSS | Merge into one. |
| 15 `window.confirm` calls | Replace with the shared confirm dialog, or undo. |
| `<style>` strings in components | Move to the layered CSS. |
| Duplicate lead fetching (`App.tsx:680`, `LeadsScreen.tsx:256`, `useWorkspaceLeads.ts`) and the 5s/15s/20s/30s polls | Replace with one cached lead store and one change feed. |

## 4. Small backend changes needed for this scope

1. Decode the snippet (`gmail.py:1423,1483`), bug b.
2. Add a single `meeting_state` on the lead response (bug c), including `cancelled`.
3. Sign-off from the rep or workspace instead of "The team" (`email_templates.py:32,41`, `meeting_email.py:410,467`, `follow_up.py:281`, `gmail.py:2655`), bug g.
4. Humanise internal field names in assistant replies (`api/routes/lead_assistant.py`).
5. Optional: a "lead conversations" filter for the Inbox.
6. Optional: an ETag or change-cursor on `/v1/leads` so the polls become cheap.
