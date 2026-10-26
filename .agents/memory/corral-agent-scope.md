---
name: Corral Agent scope and approvals
description: The requested scope and confirmation rule for Corral's app-wide assistant.
---

Corral Agent is intended to be available throughout the workspace and support guidance, content generation, and tasks across leads/qualification/routing/follow-ups; email/meetings/CRM; dashboard/analytics/reports/automations; and ICP/integrations/settings/team access. Email requests should produce an editable draft with a clear send button and optional editable Cc/Bcc fields; only include Cc/Bcc recipients when the user asks or supplies them. The user must explicitly approve sending, the sender mailbox must remain selectable when multiple Gmail accounts are connected, and the assistant must not claim success until sending succeeds. Every other data or configuration mutation must also be proposed and receive explicit user approval before execution. Chat answers and generated drafts may be returned without changing workspace state.

Corral Agent should work in a fully agentic way so users can do most of their work directly from the agent. Treat examples as representative of many different cases, not as the limits of its intended scope.

**Why:** The user said their main purpose is for users to do most of their work directly here and asked to keep the fully agentic direction in mind.

**How to apply:** Prefer completing supported workflows within the conversation over giving instructions or navigation links. Carry context through multi-step work, retrieve relevant records, prepare editable drafts, execute approved actions, verify results, and recover clearly from failures. Preserve the existing permission and approval boundaries; this goal does not mean every workflow is already implemented.

Conversation history should be resumable and available as context, including relevant prior chats, so users do not need to repeat details. If history does not resolve an ambiguous reference or a required value is missing, ask one focused question; otherwise, continue with the supported task.

**Why:** The user specifically requested chat history for context and said the agent should ask questions when there is not enough information.

**How to apply:** Retain history per user and workspace, show saved conversations in the agent UI, use relevant earlier turns when answering, and do not treat old proposals or outcomes as current authorization.

Background conversation saves must not show a generic success toast on each autosave. Keep save failures visible in the Agent's inline history status.

**Why:** The user reported repeated “saved successfully” popups while using Corral Agent.

**How to apply:** Suppress generic activity toasts for conversation persistence, while keeping operation-specific confirmations for approved actions and inline errors when chat history cannot be saved.

Customize the assistant using the saved workspace business profile and identity. General chat may retrieve relevant records on demand only within the current workspace and the user's permissions. Search is limited to bounded matches from records already stored in Corral; do not imply a complete history or a live search of external Gmail, CRM, or dialer archives.

Only show lead-open buttons when the current user explicitly asks to find, show, or open a lead, or confirms a pending match selection. Do not add them to ordinary replies merely because a lead is mentioned. Opening a button is read-only; it must not confirm a fuzzy match or approve a proposed change.

**Why:** The user clarified that lead buttons should appear only when specifically requested, not with every message.

**How to apply:** Build buttons only from server-side, workspace-scoped, permission-checked lead matches. Render every distinct match and make each button navigate to its own lead without authorizing a mutation.

The user wants information added to existing leads directly from workspace chat, not only navigation to a lead page. Keep lead detail changes, notes, progress, and re-evaluation behind explicit review of the target and values. Save evidenced facts as profile data; do not silently turn AI recommendations into facts.

Lead follow-up drafting, review, editing, and sending should stay inside the AI agent conversation. Do not route the user to the separate Follow-ups section for these steps.

For direct call requests, state that Corral Agent cannot place or start the call. When the user has Dialer permission, provide a clickable action to open Dialer so they can choose the lead or number and start the call themselves.

**Why:** the user asked for call requests to explain that the AI cannot place calls and to provide a clickable route to Dialer.

**How to apply:** Only show the Dialer action when the current user has dialer.call access. Keep call-history questions read-only and never invoke a call-start action from chat.

Workspace snapshots must come from the server under the current permissions, not from browser-supplied context. UI-reported action outcomes may inform follow-up conversation, but never authorize a write or replace the target API's permission checks.

**Why:** the user asked for a workspace-customized assistant that can search all records they can access on demand, update leads directly from chat, keep follow-up email work in the agent, take actions across nearly all app areas, and include Cc/Bcc in reviewed email drafts; external sends and other mutations still require explicit approval. Browser-supplied snapshots are not authoritative, and custom permission overrides can separate agent execution from lead viewing.

**How to apply:** Add capabilities through typed, permission-scoped tools backed by existing APIs. Retrieve only relevant stored records for each request, disclose when results are bounded, use the saved business profile for workspace identity, and keep a clear review-and-approve step before every mutation.

For read-only questions about whether a lead exists or for its details, search stored leads only within the current workspace and the user's lead-view permission. If an exact name search misses, compare all saved lead names in that workspace and suggest close matches, but ask before selecting or using one. When several leads match, list their names and ask which one the user means. Distinguish no match from search failure or missing permission.

**Why:** the user expects Corral Agent to verify current-workspace lead records rather than returning a generic action-target error, and asked for likely typo matches to be confirmed before use and matching leads to open directly when clicked.

**How to apply:** Keep exact and fuzzy searches server-side and workspace-scoped; never search another workspace or live external CRM/mail archives. Render verified matches as clickable lead-detail links only when the current user explicitly asks to find, show, or open a lead (or confirms a pending match selection). Do not attach buttons to ordinary replies merely because a lead is mentioned. Clicking a fuzzy suggestion only opens that lead and never approves a mutation. Do not use a fuzzy candidate as an action target until the user confirms it, and never report no match when search failed.

**Why:** The user clarified that lead buttons should appear only when specifically requested, not with every message.
