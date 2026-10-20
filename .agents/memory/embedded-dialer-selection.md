---
name: Embedded dialer selection
description: Product scope and provider requirements for Corral's global floating phone dock.
---

For this app, show one selected verified dialer in the floating phone dock across app screens. CloudTalk, Aircall, and RingCentral are the embedded choices; Twilio, Vapi, and Retell keep Corral's existing confirmed call flow. The phone bubble toggles the dock, and the open dock has an X close control.

Keep the dock closed by default, including after connecting or selecting a dialer. Open it only after an explicit user action such as clicking the bubble or the Integrations page's Open phone button.

The embedded phone session belongs to each Corral member's own provider account, not a shared workspace login. The Integrations connection authorizes workspace API actions; it must not be treated as the member's phone session or passed into the iframe. Members launch their phone from Integrations and authenticate through the provider or its configured SSO; the provider controls session persistence and expiry.

RingCentral's embedded phone requires a separately registered Embeddable application with 3-legged OAuth. The current Corral RingCentral connection uses JWT and does not provide that embed client ID.

**Why:** the user chose a single global embedded phone while retaining Corral's safer call flow for providers without a supported embedded workspace.

**How to apply:** Preserve the provider split and single-visible-phone behavior when changing dialer setup. Keep the dock closed until explicit user action, keep browser authentication user-scoped, never forward server API credentials into an iframe, and require the registered RingCentral embed app before enabling its live iframe.
