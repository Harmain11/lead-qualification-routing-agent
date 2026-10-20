---
name: Embedded dialer selection
description: Product scope and provider requirements for Corral's global floating phone dock.
---

For this app, show one selected verified dialer in the floating phone dock across app screens. CloudTalk, Aircall, and RingCentral are the embedded choices; Twilio, Vapi, and Retell keep Corral's existing confirmed call flow. The phone bubble toggles the dock, and the open dock has an X close control.

RingCentral's embedded phone requires a separately registered Embeddable application with 3-legged OAuth. The current Corral RingCentral connection uses JWT and does not provide that embed client ID.

**Why:** the user chose a single global embedded phone while retaining Corral's safer call flow for providers without a supported embedded workspace.

**How to apply:** Preserve the provider split and single-visible-phone behavior when changing dialer setup. Require the registered RingCentral embed app before enabling its live iframe; do not reuse the JWT credentials as a substitute.
