---
name: Gmail AI lead sync
description: Product behavior for Gmail lead detection and safe handling of AI failures.
---

Keep non-deterministic AI classification as the primary Gmail lead detector and favor recall for plausible early-stage business inquiries; do not add a rigid keyword gate after an AI-positive result. Verify AI evidence as a contiguous source excerpt after normalizing Unicode punctuation and whitespace, but do not accept paraphrased evidence. Missing or invalid AI credentials must be surfaced as a configuration problem; a transient per-message AI failure must skip and audit that message while allowing the rest of the sync to continue. Never log email bodies or credentials.

**Why:** The user explicitly asked to preserve AI-based lead detection, reported missed leads, and shared a run with evidence-matching failures; strict byte-for-byte excerpt matching can reject valid evidence with harmless formatting differences.

**How to apply:** When changing Gmail acquisition, classification, background processing, or status reporting, preserve AI classification, high recall without a regex veto, normalized-but-source-grounded evidence checks, safe per-message audit reasons, bounded retries, and clear progress/error states.
