# Corral UI/UX overhaul plan

Branch `ui/overhaul` (from `fix/agent-truthfulness-security-speed`). Evidence: `audit/01-04`. Approved scope includes the owner's additions of 2026-10-29: native email client features, a coherent dialer and call history, lead highlights, Ask Corral as a full agent, rebuilt Integrations, and real coaching.

## Direction
- **One design system: v3.** v3 already has the right foundation (`v3/foundation.css` tokens, `v3/ui/primitives.tsx`, the v3 shell). Every legacy screen moves onto it, and the legacy and patch layers are deleted.
- **Apple-grade principles.** Clarity, deference to content, one primary action per view, progressive disclosure, immediate feedback, undo over confirm for safe actions, and a designed confirm for destructive or outbound ones.
- **Motion (emil-design-eng).** Only transform and opacity. UI motion stays under 250ms and uses strong ease-out. Nothing animates on keyboard-driven actions. Buttons press to `scale(0.97)`. Popovers grow from their trigger. Reduced-motion uses fades.
- **Truthful agent state.** One `ActionCard` with explicit states (explanation, proposed, draft, awaiting confirmation, working, completed, failed, uncertain). Completed shows only when the API reports success. URL-guessed toasts are removed.

## Milestones (each one is typechecked, tested, screenshot-verified, committed and pushed)
1. **Foundation.**
   - Legacy tokens become aliases of the v3 tokens, with a single dark block.
   - One font stack, the System theme option, and a `prefers-color-scheme` default.
   - Delete the dead views and components: `LeadsView`, `PipelineView`, `ManualReviewView`, `ContactsView`, `AgencyView`, `LeadDetail`, `NavigationMenuItem`, `NetworkDomFallback`, `networkData`, `ProductViews`, `ContactsView.css`.
   - Add the consistency gate script.
2. **Primitives.**
   - `ConfirmDialog` replaces all 19 `window.confirm` calls.
   - Truthful `toast()` API with undo/retry; errors persist until dismissed.
   - `ActionCard` for the eight states above.
   - `format.ts`: one module for money, percent, counts, dates and relative time.
   - `Field`/`Form`.
   - One dialog and sheet implementation.
3. **Speed and shell.**
   - Per-view data queries instead of the 11-request boot burst.
   - A shared lead store with working in-flight dedupe (fix `api.ts:260`).
   - One polling scheduler that pauses on hidden tabs and offline.
   - Lazy-load overlays; the phone iframe loads on first use.
   - `range` only on the views that use it.
   - One search entry point; Integrations goes back to a plain sidebar link.
   - Truthful Online/Offline indicator.
   - Footer cleanup and a company contact address.
4. **Lead record.**
   - A highlights strip at the top: hold reason, meeting state (including cancelled), missing info, next best action.
   - One backend `meeting_state`; lazy-mounted tabs.
   - Fix bugs a–g from audit 03.
   - The email composer keeps its verb ("Send email") and shows why it is held.
5. **Email client.**
   - Reply, reply all, forward, drafts list, cc/bcc.
   - Formatting toolbar and attachments.
   - Compose with AI that uses the full thread, with an instruction box and undo.
   - Calendar insertion: booking link and proposed slots.
   - Sanitise rendered HTML, decode snippets, and default the inbox to lead conversations.
6. **Calls.**
   - One phone surface with call, copy, add to lead and assign lead.
   - Call history with inbound, outbound and missed filters and call detail.
7. **Ask Corral agent.**
   - Command over navigation (open and close any view, lead, tab, dialog).
   - Drafting, editing and discarding; updating and moving stages; sequences and playbooks.
   - All actions run through the `ActionCard` and the existing permission and confirmation rules.
   - Coaching: deal coaching and next-step guidance, call and meeting minutes with advice, objection handling, and rep mentoring digests. All grounded in recorded evidence.
8. **Integrations rebuild.**
   - New page on v3: categories, real status (failed and expired are errors), verified-only success.
   - Provider logos where licensable, otherwise neutral glyphs.
   - Per-provider setup sheets; "coming soon" providers collapsed.
9. **Remaining legacy screens on v3.** Analytics (with sample guards), AI Activity, Research, Automations (templates, real empty state), Team, Settings, ICP, Policies, Compliance, Calendar (week view), Contacts/Companies, Auth (forgot password), legal pages.
10. **Hardening.**
    - Accessibility sweep.
    - Full screenshot matrix (5 widths, 2 themes).
    - Throttled performance re-measure.
    - Delete the patch CSS layers.
    - Docs update.

## Small backend work
- Decode snippets.
- `meeting_state` on the lead response.
- Analytics excludes cancelled meetings and returns `null` below the sample size.
- Sign-off uses the rep or workspace name instead of "The team".
- Humanised assistant field names.
- Password reset, reusing the verification-token mechanism.
- New assistant action kinds for navigation and coaching.
- Inbox lead-conversation filter.
- Optional cheap change-cursor on `/v1/leads`.

## Not in this overhaul (needs a separate project)
- SSO/SAML/SCIM and MFA.
- Real legal text (needs counsel).
- SOC 2.
- Native mobile apps.
