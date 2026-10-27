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
