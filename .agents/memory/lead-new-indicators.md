---
name: New lead indicators
description: Lead numbers stay out of the UI; unseen new leads use per-lead and tab dots.
---

Do not display sequential lead numbers. Show a dot beside each new, unseen lead and a navigation dot for the Leads area; Manual Review also gets a dot when it contains unseen new leads. New arrivals also appear in a bell inbox and trigger one toast when first observed. Opening a lead clears its inbox item and dot; clear each tab dot only when all relevant new leads have been opened.

**Why:** The user chose per-lead and tab indicators instead of lead numbers, then requested a bell inbox and toast for new arrivals.

**How to apply:** Keep numeric identifiers internal. Preserve the inbox and dot behavior across visits, scope them to the active user and workspace, and notify only for newly observed leads.

## Repeat imports

Suppress a second new-lead toast or bell item when the same email is re-imported after its lead record was deleted. Persist a normalized-email fingerprint in the existing per-user, per-workspace indicator snapshot; do not store the raw email there. This rule applies only to arrival indicators and must not suppress assignment or Slack notifications.

**Why:** Deleting and re-syncing a Gmail lead creates a new internal lead ID, which otherwise looks like a new arrival. The user chose to suppress only the repeated toast and bell, preserving assignment alerts.

**How to apply:** Hash trimmed, lowercase email addresses for stable arrival deduplication, and clear any stale unread arrival item when the same identity returns under a new ID. Keep unrelated contacts and assignment/Slack notification behavior unchanged.

Reconcile unread arrival IDs against complete workspace lead snapshots so deleted leads do not leave stale sidebar or bell counts. Never prune against a paginated subset; preserve valid arrivals outside the loaded page, and keep identity fingerprints after clearing a deleted lead's unread item.

**Why:** A deleted lead can remain in the user's persisted unread snapshot after the workspace becomes empty, leaving incorrect counts in both navigation and the notification bell.

**How to apply:** Treat the lead API's `total <= items.length` as a complete snapshot. Clear missing unread IDs only then, and continue using the persisted identity fingerprints to suppress duplicate arrival alerts after re-import.
