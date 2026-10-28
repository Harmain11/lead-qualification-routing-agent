---
name: Workspace manual-review settings
description: Product and configuration rules for workspace-configurable lead analysis review criteria.
---

Analysis Manual Review criteria are workspace-configurable and remain separate from Integration provider import-approval settings. If a workspace has no explicit stored review override, preserve the shared routing configuration as the base. Changing criteria applies to new and requalified leads; it does not silently recalculate existing saved review decisions.

**Why:** The user chose to change analysis review rules while keeping import-approval settings separate, and existing review states should not be rewritten without an explicit request.

**How to apply:** Keep both policies independent. Treat missing or empty workspace review settings as no override, and do not bulk-re-evaluate existing leads unless the user asks.

Completing a lead's manual review is a per-lead decision: clear that lead's active analysis and routing hold, record who completed it, then apply current routing. This may notify the assignee; it must not change workspace review criteria or trigger a new analysis.

**Why:** The user requested an explicit way to mark a lead's review done, including through Corral Agent, without changing the workspace's review rules.

**How to apply:** Require lead-view and manual-review-management permission. Keep the lead-page button direct and require explicit approval for assistant actions; explain that routing and assignee notifications may follow.

When completing a review, synchronize both the analysis row's review fields and the corresponding values in its persisted structured output before rerouting.

**Why:** Routing reconstructs its analysis from structured output, so clearing only the row-level flag allows the same review hold to be recreated immediately.

**How to apply:** Clear `requires_manual_review` in both places, and change structured-output status from `manual_review` to `completed` when applicable.
