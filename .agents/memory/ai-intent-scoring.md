---
name: AI-owned intent scoring
description: Intent and urgency points must be assigned by the active reasoning provider.
---

Intent and urgency subscore points must come directly from validated AI analysis for each criterion. Application code may validate the output and configured maximums, but must not infer or override points with keywords, lead stage, or deterministic rules. If a validated AI result is unavailable, award no intent points and preserve the provider-error/review flow.

**Why:** The user explicitly rejected hybrid scoring after production intent scores disagreed with the AI's own context assessment.

**How to apply:** Keep scoring points in the structured provider output, bump the reasoning prompt version when its rubric changes, and re-analyze stale leads after deployment so their persisted scores use the new rubric.
