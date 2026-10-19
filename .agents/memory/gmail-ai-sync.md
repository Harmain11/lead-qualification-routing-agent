---
name: Gmail AI lead sync
description: Product behavior for Gmail lead detection and safe handling of AI failures.
---

Keep non-deterministic AI classification as the primary Gmail lead detector and favor recall for plausible early-stage business inquiries; do not add a rigid keyword gate after an AI-positive result. Missing or invalid AI credentials must be surfaced as a configuration problem; a transient per-message AI failure must skip and audit that message while allowing the rest of the sync to continue. Never log email bodies or credentials.

**Why:** The user explicitly asked to preserve AI-based lead detection, reported that many leads were being missed, and asked to distinguish sync/provider failures rather than hide them with deterministic fallback.

**How to apply:** When changing Gmail acquisition, classification, background processing, or status reporting, preserve AI classification, high recall without a regex veto, safe per-message audit reasons, bounded retries, and clear progress/error states.
