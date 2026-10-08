---
name: OAuth account choice
description: Browser-session selection limits for Calendly and Slack authorization.
---

Show an explicit account choice before Calendly or Slack Connect/Reconnect.
For a different account, explain that the user must switch accounts at the
provider before continuing; do not silently reuse the browser session.

**Why:** The user wants to choose an account rather than automatically connect
whichever account is already logged in. Calendly's documented authorization
parameters do not include a forced login/account-picker option. Slack explicitly
allows workspace selection when its optional `team` parameter is omitted.

**How to apply:** Do not invent unsupported OAuth prompt parameters or claim
Corral can clear another domain's cookies. Keep Slack workspace selection open
and keep the Calendly account-switch step explicit.
