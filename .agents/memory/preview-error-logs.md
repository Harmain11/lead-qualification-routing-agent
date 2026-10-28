---
name: Preview error logs
description: Keep request and exception fields visible in Replit preview logs.
---

The Replit preview should use JSON logging so middleware request fields, including the failing path and sanitized error details, are visible. Plain-text logging emits only the generic event name.

**Why:** a preview assistant request returned a generic 500, but text-mode logs omitted the endpoint and error fields needed to identify the cause.

**How to apply:** Keep `LOG_FORMAT=json` in the preview runner. Do not expose internal exception details to users in the UI.
