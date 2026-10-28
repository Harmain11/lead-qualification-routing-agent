---
name: Workspace ICP scoring activation
description: Product policy for qualification before and after a custom workspace ICP is published.
---

Until a workspace publishes a custom ICP, do not score or hard-exclude leads using the built-in profile. Use urgency and engagement quality only, normalize their active points to the 100-point qualification scale, omit ICP fit as an unavailable component, and tell users in lead notifications how to publish a custom ICP. Publishing a custom ICP activates fit scoring and its exclusions for future qualification runs. Existing stored qualification results remain historical and are not silently rewritten.

**Why:** The user requested that qualification skip ICP scoring rather than use a default ICP, while still evaluating urgency and engagement.

**How to apply:** Treat only a published workspace ICP as active. Keep any built-in profile available only as an explicitly labeled template, and preserve the not-configured status through notifications, CRM payloads, and lead details.
