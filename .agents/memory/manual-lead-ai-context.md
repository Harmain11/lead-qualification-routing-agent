---
name: Manual lead AI context
description: Rules for AI analysis of manually entered inquiry text and lead fields.
---

Manual Control Center lead creation requires successful analysis by the configured workspace AI provider. All submitted details are optional, but at least one meaningful lead detail is needed to create a record. Missing values remain unknown and do not match a hard disqualifier solely because they are absent; the published scoring rubric may still award fewer points for unknown criteria. The inquiry text may be a direct customer message or a human-written summary of the sales process, completed steps, current stage, and open questions; it must not be treated as a verbatim quote by default. Keep qualification scoring and hard-disqualification safeguards deterministic.

Saved team comments, sales-progress updates, and recent customer messages are current evidence for qualification scores, lead and workspace assistant answers, decisions, and follow-up drafts. Newer explicit evidence can supersede older intent. Do not increase scores just because a note is longer, and distinguish seller actions from prospect commitments.

**Why:** Leads may arrive with incomplete details, and manual process summaries contain useful buying-stage evidence but can misattribute words or progress if interpreted as exact customer speech. A deterministic-only result is not an acceptable substitute for requested AI context analysis.

**How to apply:** Include submitted context and source metadata in provider payloads, explain both partial entry and summary-vs-quote handling in the UI and prompts, do not count missing-value placeholders as completed contact details, and do not create a manual lead when the AI provider is missing or analysis fails.

**Why:** the user said comments and updates reflect current information that can change decisions, answers, follow-ups, and qualification scores.

**How to apply:** Feed timestamped comments, progress, and customer replies into scoring and AI contexts; keep them as untrusted evidence, preserve the original inquiry, and let newer explicit evidence override stale intent.
