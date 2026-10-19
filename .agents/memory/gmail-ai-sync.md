---
name: Gmail AI lead sync
description: Product behavior for Gmail lead detection and safe handling of AI failures.
---

Keep non-deterministic AI classification as the primary Gmail lead detector and favor recall for plausible early-stage business inquiries; do not add a rigid keyword gate after an AI-positive result. Analyze the whole thread, including message metadata and available inline text attachments. When a forwarded prospect is distinct from its forwarder, use the prospect identity and preserve the thread as source evidence; do not create leads for people mentioned only in CC, signatures, or quoted history. Verify AI evidence as a contiguous source excerpt after normalizing Unicode punctuation and whitespace, but do not accept paraphrases. Missing or invalid AI credentials must be surfaced as a configuration problem; a transient per-message AI failure must skip and audit that message while allowing the rest of the sync to continue. Never log email bodies or credentials.

**Why:** The user explicitly requires thread-aware Gmail review so forwarded prospects are not lost or merged into their forwarders; strict byte-for-byte evidence matching can also reject valid evidence with harmless formatting differences.

**How to apply:** When changing Gmail acquisition, classification, background processing, or status reporting, preserve AI classification, high recall without a regex veto, whole-thread context, explicit forwarded-identity validation, idempotent message links, normalized-but-source-grounded evidence checks, safe per-message audit reasons, bounded retries, and clear progress/error states.
