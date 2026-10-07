---
name: OAuth production diagnosis
description: Distinguish new browser authorization failures from successful background token refreshes.
---

A successful Salesforce worker token refresh or CRM query is not proof that a
new browser OAuth attempt succeeded.

**Why:** Production workers continued syncing an older verified connection while
new browser attempts remained awaiting authorization; conflating the two hid
the failed callback path.

**How to apply:** Correlate callback logs with the new connection's status, rather
than treating any provider HTTP 200 in the same time window as OAuth success.
Historical callbacks without recorded failure reasons need a fresh attempt;
do not infer their provider error from successful worker requests.
