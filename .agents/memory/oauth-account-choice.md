---
name: OAuth account choice
description: Account-choice placement for Calendly, Salesforce, and Slack authorization.
---

Show an explicit account choice before Calendly Connect/Reconnect and inside
the Salesforce package setup screen. Do not add Corral's extra account-choice
step to Slack; use Slack's own authorization workspace picker.
For a different account, explain that the user must switch accounts at the
provider before continuing; do not silently reuse the browser session.

**Why:** The user wants to choose an account rather than automatically connect
whichever account is already logged in. Calendly's documented authorization
parameters do not include a forced login/account-picker option. The user asked
to move the extra Slack choice to Salesforce because Slack already offers one.
Slack explicitly
allows workspace selection when its optional `team` parameter is omitted.

**How to apply:** Do not invent unsupported OAuth prompt parameters or claim
Corral can clear another domain's cookies. Keep Slack workspace selection open
and keep the Calendly and Salesforce account-switch steps explicit.
