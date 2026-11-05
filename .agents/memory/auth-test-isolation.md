---
name: Authentication test isolation
description: Avoid environment-configured site gating obscuring authentication route tests.
---

Use a fresh, explicitly configured test app for isolated authentication-route tests.

**Why:** The global app's access-gate middleware captures its startup settings. On Replit, an existing site-access secret can make route tests return HTTP 423 even when authentication dependencies use test settings. Dependency overrides alone do not replace those captured middleware settings.

**How to apply:** Keep the real application's gate intact. Mount the relevant router in a test-only app for isolated auth tests, and verify site gating separately.
