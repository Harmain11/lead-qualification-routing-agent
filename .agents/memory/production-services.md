---
name: Production service constraints
description: Cost preference and connector-package availability for production setup.
---

The user wants free Replit-native options where possible, rather than paid external Redis/email subscriptions. Gmail was selected and connected for application verification email after explaining that it sends from the connected mailbox and has sending limits.

**Why:** The user said the recommended external services were paid and requested free options Replit can handle.

**How to apply:** Prefer the connected application sender and existing VM resources before proposing another provider. Do not describe Replit hosting itself as free, or claim local Redis is durable across redeploys. Preserve verification and other production safety checks.

The generated Gmail integration instructions named a Python connector package that could not be resolved in the managed package registry. The official JavaScript connector SDK installed and successfully accessed the connected Gmail profile.

**Why:** Installing the documented Python package failed because the package was absent, not because credentials were missing.

**How to apply:** For Python services, use the official JavaScript SDK through a server-only bridge when this registry limitation persists; do not invent credential-handling code or ask for Gmail passwords.

Cold startup on the production VM can be substantially slower than a warm workspace
test. A deployment passed every dependency check but its initial health checks
exhausted their attempts about four seconds before Uvicorn finished starting.

**Why:** Successful configuration and connectivity checks did not prevent a visible
500 when initialization exceeded the publishing health-check window.

**How to apply:** Compare deployment timestamps for the first health check, dependency
probes, and the public listener becoming ready. Do not equate a warm TestClient smoke
test with proof that a cold production boot will meet the health-check deadline.
