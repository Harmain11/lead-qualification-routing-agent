---
name: Analytics metric integrity
description: Corral dashboard and analytics must not invent values when the required source data is absent.
---

Use recorded workspace data only for dashboard and analytics cards. When a metric depends on information the product does not store, show it as unavailable or not tracked and explain the missing input rather than estimating or relabeling a nearby metric.

**Why:** The user explicitly asked that analytics use real stored data and not invent values for missing inputs.

**How to apply:** When adding a metric, trace its numerator, denominator, time window, and attribution to persisted records or configuration. If any required part is absent, state the limitation in the card.

Commercial value is not the prospect's stated budget. A forecast must identify the recorded cohort behind its close rate; fixed stage percentages are not measured closing probabilities.

**Why:** Deal Desk totals must represent saved commercial facts, consistently with the user's existing requirement for grounded metrics.

**How to apply:** Use quotations, explicit deal values and recorded outcomes. Show an unavailable forecast until there is an evidenced close rate, and name the cohort when one is used.
