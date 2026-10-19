---
name: Gmail AI lead sync
description: Product behavior for Gmail lead detection and safe handling of AI failures.
---

Keep non-deterministic AI classification as the primary Gmail lead detector and favor recall for plausible early-stage business inquiries; do not add a rigid keyword gate after an AI-positive result. Analyze the whole thread, including message metadata and available inline text attachments. Treat configured internal domains and known workspace members as internal. A quoted or forwarded external sender may be selected only when the full conversation supports them as the actual prospect; preserve that identity and the source thread. Do not create leads for people appearing only in CC, signatures, or unrelated mentions. Verify AI evidence as a contiguous source excerpt after normalizing Unicode punctuation and whitespace, but do not accept paraphrases. Missing or invalid AI credentials must be surfaced as a configuration problem; a transient per-message AI failure must skip and audit that message while allowing the rest of the sync to continue. Never log email bodies or credentials.

**Why:** The user explicitly requires thread-aware Gmail review so an external prospect in quoted/forwarded history is not lost or merged into an internal forwarder, while internal staff and unrelated mentions remain excluded. Strict byte-for-byte evidence matching can also reject valid evidence with harmless formatting differences.

**How to apply:** When changing Gmail acquisition, classification, background processing, or status reporting, preserve AI classification, high recall without a regex veto, whole-thread context, internal-domain/member filtering, explicit forwarded-identity validation, idempotent message links, source-grounded evidence references, safe per-message audit reasons, bounded retries, and clear progress/error states.
