---
name: Browser save feedback safety
description: Keep optional UI notification failures separate from successful server-side mutations.
---

Optional status notifications must not cause an already-saved action to be reported as failed.

**Why:** Browser checks without secure-context UUID support exposed a shared notification exception after successful server responses, making saved rules and lead outcomes appear unsuccessful.

**How to apply:** Use identifier generation compatible with embedded browsers, isolate optional feedback failures from mutation results, and test successful saves with the secure-context UUID API unavailable.
