# Phase 7A4 — Lead momentum and delivery evidence

## Product decision

Corral should not present a one-time AI score as a final answer. A lead can
become more or less actionable after an email reply, a salesperson update, or
a connected-system delivery result. This increment makes those changes visible
without adding autonomous outreach or silently changing workspace policy.

## Scope

1. Provide a workspace-scoped momentum read model for each lead.
2. Explain the current temperature using persisted score evidence and verified
   events, including the prior observed score when one exists.
3. Present a single delivery ledger for Slack, automatic confirmation email,
   and CRM handoff.
4. Reuse the existing pipeline-history API contract used by the lead detail
   view, so updates and Gmail messages remain inspectable after a refresh.
5. Keep routing read-only: the view previews the current approved policy; it
   does not publish rules, send notification retries, or send outreach.

## Acceptance checks

- A viewer can retrieve only momentum and history belonging to their workspace.
- A manual progress event that causes requalification exposes the score delta
  and the verified trigger in the momentum response.
- Evidence returns excerpts from persisted qualification/analysis data, not
  invented explanations.
- The delivery ledger represents not-attempted, pending, sent/synced, and
  failed/retryable states without treating a missing connection as a success.
- The lead drawer shows momentum, evidence, a next human action, and the
  delivery ledger in light and dark themes.

## Explicitly deferred

- Automatic changes to ICP or routing rules.
- Autonomous follow-up sends or automated retry of uncertain delivery.
- Conversion-model learning; this needs sufficient reviewed outcomes and a
  separate approval workflow before it can affect production decisions.
