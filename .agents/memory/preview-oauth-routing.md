---
name: Replit preview OAuth routing
description: Keep preview OAuth callbacks routed to the primary app when a mockup server also runs.
---

The public Replit development domain routes to the configured external port 80, not necessarily the main workflow's `waitForPort`. When a component-preview server uses that root port, OAuth callbacks can land on its Vite base-path handler instead of the app API. Keep the primary app on the root domain and serve component previews beneath their dedicated path through a proxy.

**Why:** A Gmail OAuth callback reached the mockup Vite server and failed with a base-path suggestion because the root port mapped to the component-preview workflow.

**How to apply:** Check `.replit` external-port mappings before changing OAuth callback code. Confirm the root domain reaches the app, `/v1` reaches the API, and `/__mockup/` still serves canvas previews.
