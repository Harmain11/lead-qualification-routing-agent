---
name: Gmail AI lead sync
description: Product behavior for Gmail lead detection and safe handling of AI failures.
---

Keep AI as the intent and field-extraction layer, but choose identity deterministically from parsed external senders/respondents, internal-authored external To recipients, and evidence-backed referrals. Parse raw and nested forwarded/quoted headers without relying on fixed names, sender addresses, or subject strings; never treat Cc, signatures, notification wrappers, or internal workspace members as leads by themselves. Internal commercial outreach to an external To recipient can qualify even before a reply; retain the internal rep as source metadata. Analyze the whole thread, assign roles, and allow multiple independently supported leads. Treat configured internal domains and workspace members as internal, but retain external referrers as context. Unwrap internal lead-notification wrappers and extract the embedded external person while retaining wrapper provenance. Referred candidates without email are allowed only with supported name plus reliable phone/company/referral evidence; uncertain identity requires manual review. Preserve candidate-specific evidence, role, referral relationship, thread/message provenance, and stable candidate-level idempotency. Verify AI evidence as contiguous source excerpts after normalizing Unicode punctuation and whitespace, but do not accept paraphrases. Gmail GET requests should retry transient quota/server responses only a bounded number of times, honoring a short Retry-After; terminal run failures must not be presented as completed reviews. Missing or invalid AI credentials must be surfaced as a configuration problem; transient per-message AI failure must skip and audit that message while allowing the rest of the sync to continue. Never log email bodies or credentials.

**Why:** The user requires prospect identity to come from message direction and evidence, so internal reps and notification wrappers cannot replace the external person being contacted or responding. Gmail may also return transient rate limits; a failed review must remain clearly failed and transient GET failures should receive bounded retry.

**How to apply:** When changing Gmail acquisition, classification, background processing, or status reporting, preserve AI classification, high recall without a regex veto, whole-thread context, internal-domain/member filtering, candidate role/referral evidence, phone-only support with review for uncertainty, candidate-level idempotency, safe per-message audit reasons, bounded retries, and clear progress/error states.

## Independent candidate validation

Validate child prospects against their own evidence, not redundant legacy thread-level identity/inquiry fields. Unsupported optional enrichment should be left unknown with a review note, not veto a verified identity. Identity, phone-only identity, inquiry, and referral evidence remain strict. Save valid Gmail candidates in a partially failed page without advancing its checkpoint.

**Why:** Production reviews discarded candidates for optional or redundant extraction evidence errors, and an earlier quota failure prevented later successfully normalized prospects in the same batch from being saved.

**How to apply:** Keep regressions for valid children with invalid parent metadata, optional hallucinations, fabricated identities, and a rate-limited message before a valid prospect. Sent mail alone must not exclude an explicitly forwarded, direction-supported external prospect.
