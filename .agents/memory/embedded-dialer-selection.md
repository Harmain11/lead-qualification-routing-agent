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

When an embedded phone is selected, do not repeat its phone card or phone workflow instructions in the Dialer Overview; the selected phone is available in the global dock. Keep the DirectDialer card for providers that use Corral's own call flow, and preserve the CloudTalk keypad safety warning in the global phone dock.

**Why:** the user asked to remove the CloudTalk Phone section from the Dialer tab without removing the floating phone that is available throughout Corral.

**How to apply:** Hide the embedded-phone panel and its redundant overview cards when CloudTalk, Aircall, or an enabled RingCentral embed is selected. Do not remove call history or transcript analysis from their own tabs.

In the Dialer lead picker, selecting a lead should show only its summary with “Open lead” and “Choose another lead.” Do not open a per-lead call/activity panel there.

**Why:** the user asked to remove the panel that opens after choosing a lead and leave only those two choices.

**How to apply:** Keep call history and provider controls in their existing sections; direct per-lead calling is handled from the lead record or the selected phone.

The V3 lead Calls tab is for call history and transcript/AI analysis only. Do not place call-preparation or call-again controls in that tab; keep outbound calling in the Dialer or the global phone.

**Why:** The user asked to remove the call-preparation panel from the lead record and show call history and transcript analysis instead.

**How to apply:** Use the history-only CallsPanel mode in lead records while preserving its call-capable mode in the Dialer. Keep history sync permission-gated and require explicit user action before loading provider details.
