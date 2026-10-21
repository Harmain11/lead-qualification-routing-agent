---
name: Manual lead AI context
description: Rules for AI analysis of manually entered inquiry text and lead fields.
---

Manual Control Center lead creation requires successful analysis by the configured workspace AI provider. The inquiry text may be a direct customer message or a human-written summary of the sales process, completed steps, current stage, and open questions; it must not be treated as a verbatim quote by default. Keep qualification scoring and hard-disqualification safeguards deterministic.

**Why:** Manual process summaries contain useful buying-stage evidence but can misattribute words or progress if interpreted as exact customer speech. A deterministic-only result is not an acceptable substitute for requested AI context analysis.

**How to apply:** Include the submitted context and source metadata in provider payloads, explain the summary-vs-quote distinction in prompts and UI, and do not create a manual lead when the AI provider is missing or analysis fails.
