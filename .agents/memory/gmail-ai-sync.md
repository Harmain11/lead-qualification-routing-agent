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

## Early-stage service discussions

Treat plausible conversations about the workspace's services as leads even when the service is incomplete or the conversation is at its starting point. A request for more context can qualify using the whole thread. Missing service details stay unknown; they are not a reason to reject the lead. Keep a referring participant linked as context and track the supported referred contact separately.

**Why:** The user specified that these are startup calls: if the email is about the team's services, the contact can be a lead even while information is missing.

**How to apply:** A relevant reply confirming or scheduling a services call, asking for context, or continuing the discussion is enough; don't require a stated buying need. Separate lead detection from sales readiness. Verify exact evidence after removing quoted-line transport prefixes, retain strict identity checks, and distinguish actual rejection reasons from optional enrichment notes. Normalize phone formatting only when the same number appears in the source. Allow one evidence-repair pass over the acquired thread.

## Internal company forwarders and missing details

Ingenuity and Ingenuity Systems company email addresses belong to the internal team; never promote their representatives or forwarders into prospect identities. Their forwarded conversations are lead source material, not grounds to reject an embedded external opportunity. A verified external email can be retained with the person's missing name explicitly marked unknown.

**Why:** The user said “all forwarded mail with ingenuitysystem or ingenuity are our own companies email dont take there name in leads” and reiterated that startup messages can have missing information.

**How to apply:** Recognize the company-domain variants independently of manual internal-domain setup, including quoted headers and name-only internal representatives. Keep their identities as provenance. Do not invent missing personal or service details.

## Follow-up email ownership

Import later messages into the matching lead's Gmail history rather than making a duplicate lead. Scope a message by the exact external email in From or To within the same workspace; a Cc-only address is not an identity match. Do not guess when an email is ambiguous across existing leads.

**Why:** The user emphasized that a Gmail provider handles many prospects and each email must be attached to the particular lead it actually matches.

**How to apply:** Keep distinct candidates in the same thread attached to their own email identities, deduplicate imported messages by mailbox and Gmail message ID, and preserve already-known lead details when a follow-up omits them.

## Personal email review and optional contact fields

Route Gmail candidates using common consumer mailbox domains to manual review before standard sales routing. Keep existing uncertainty-based review for work-email identities. Extract Lead Details fields—name, email, phone, company, job title, product/service, industry, geography, employee count, annual revenue, and budget—when exact source evidence supports them; otherwise leave them unknown. Distinguish organization names from personal names in sender display names and signatures, and never infer industry from company name or domain.

**Why:** The user confirmed personal email deserves human verification, while available contact details should be captured without blocking an otherwise valid candidate.

**How to apply:** Treat consumer-provider domains as review signals, not proof of identity or a reason to reject a lead. Preserve evidence grounding and optionality in the extraction schema, prompts, and tests.

## Email evidence presentation

Email formatting in Lead Details is presentation-only. Preserve the original message and source metadata for raw review; safe readable text, links, quotes, forwards, and footer disclosures must not alter content used for lead identity, qualification, or extraction.

**Why:** The user requested a readable evidence card while keeping the underlying Gmail source intact and not changing lead extraction behavior.

**How to apply:** Transform only a display copy, never inject raw HTML, and keep quoted/forwarded sections, footer notices, and full source accessible. Cover normal messages, nested forwards and quotes, duplicate disclaimers, long URLs, and narrow screens.

## Historical Gmail scans

Provide an explicit AI-review action for messages older than the recent 30-day window. Keep historical paging separate from the live Gmail history cursor; preserve the same evidence, deduplication, manual-review, cancellation, and no-confirmation-email safeguards. Bound each batch and allow repeated runs to continue farther back.

**Why:** The user requested a clear way to sync previous Gmail leads, not only new or recent email.

**How to apply:** Keep historical and incremental Gmail sync independent. Historical scans must not overwrite live-sync checkpoints or schedule state, and must continue through the existing review pipeline.
