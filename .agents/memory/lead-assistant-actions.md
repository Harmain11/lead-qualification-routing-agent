---
name: Lead assistant action safety
description: User-selected confirmation boundaries for actions proposed by Corral on lead pages.
---

Corral may answer questions, add details, and propose actions across the lead page. Keep common tasks directly accessible in the agent, including reviewed email drafting/sending and clear phone-number copy/call guidance. Require explicit user confirmation before sending emails, booking meetings, syncing a CRM record, or deleting a lead. Proposed actions must not be described as completed until the corresponding action succeeds.

**Why:** The user wants the lead agent to make useful actions easy to do directly in the conversation, while retaining confirmation for emails, bookings, CRM syncs, and deletes.

**How to apply:** Preserve existing permission checks and action-specific confirmation steps whenever adding or changing lead-assistant actions.

Notes and comments requested in chat must appear as editable, clearly unsaved drafts, like email drafts. Save only after explicit approval, allow discarding, and do not change sales status for a regular comment.

**Why:** The user asked for draft notes/comments like emails and reported that applying note/comment changes was not working.

**How to apply:** Use the team-note save path, not profile-field updates, preserve text on failed saves, and show saved comments promptly.

Interpret obvious typos in context; do not blindly agree with a mistaken statement. Ask when meaning is unclear. Acknowledgements, corrections, and “done” do not prove an action succeeded or authorize a new change.

**Why:** The user asked the assistant to understand their text before answering rather than treating mistakes as correct.

**How to apply:** Do not resurrect earlier proposals or lead searches for ordinary follow-up conversation; use actual UI outcomes for completion claims.

Treat supplied failure examples as examples of a general conversation-and-action problem, not as the only cases to support. Use follow-up details in context and explain actual capability limits rather than promising unsupported work.

**Why:** The user said the screenshot was “just one test case,” there could be many others, and the assistant should have the ability to handle them.

**How to apply:** Generalize across supported operations, distinguish creation from existing-record updates, retain reviewed drafts on failure, and test varied wording, partial details, permissions, and corrections.

Delivery rows should place relevant actions beside their statuses: retry failed team notifications, make CRM writes explicit, and open an editable email composer rather than auto-sending. Preserve manual-review/disqualification blocks and each action's permission gate.

**Why:** The user asked to handle failed CRM delivery and related actions directly from the corresponding delivery rows.

**How to apply:** Keep retry/send controls next to the delivery they affect, retain provider errors in the row, and never bypass the email review-and-send step.
