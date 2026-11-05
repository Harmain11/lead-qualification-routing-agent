---
name: Corral site access gate
description: The user's choice for protecting the Corral website.
---

Use a custom Corral UI and server-side access check with the shared password stored in Replit Secrets. Do not use Replit's built-in deployment password gate.

**Why:** The user explicitly wants Corral's own gate and does not want Replit's gate.

**How to apply:** Preserve the app-owned gate for future access-control changes, and handle password setup or rotation through Replit Secrets.
