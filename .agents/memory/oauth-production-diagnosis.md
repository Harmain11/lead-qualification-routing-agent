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

Error-handling improvements are not a fix for provider-side authorization policy.
Do not describe a new Salesforce connection as resolved solely because local
tests pass or the published site is healthy.

**Why:** A fresh production authorization remained denied after the callback
diagnostics were published successfully.

**How to apply:** Confirm a fresh authorization reaches a verified connection;
otherwise distinguish the tested app-side changes from the unresolved provider
restriction and request only the missing provider policy information.

The user confirmed on 2026-10-07 that Corral's Salesforce app uses
**All users may self-authorize** as its Permitted Users policy.

**Why:** The user checked and provided the external Salesforce setting.

**How to apply:** Do not assume admin preauthorization or profile assignment is
the cause of the denial. Check the specific provider error and other app settings;
reconfirm the policy if the Salesforce configuration changes.
