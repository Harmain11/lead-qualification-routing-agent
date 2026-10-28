---
name: Email drafting style
description: Product rules for AI-generated workspace email templates and lead follow-ups.
---

Use the workspace's current sample as a tone and voice reference, but write fresh wording rather than copying it or only replacing merge fields. Preserve the exact merge-field placeholders present in the sample. Keep generated samples reviewable before saving, and keep lead follow-ups human-reviewed before sending.

Email drafts must be AI-generated, with manual editing available. Do not silently create a template draft when AI is unavailable or fails. Allow multiple follow-up emails without replacing previously sent messages.

Use the saved workspace business profile and email signature for AI-generated email content. Never emit generic sender placeholders such as "[Your Name]"; if no signature is configured, use the known business name when available and otherwise omit the signature.

**Why:** The user said, “Email drafts should only be AI generated, with manual editing option,” and requested multiple follow-ups.

**Why:** The user asked for AI-generated email that reflects the workspace's tone instead of returning the same template text.

**How to apply:** Pass the current subject and body to template generation, including unsaved editor text. For per-lead AI follow-ups, pass the saved sample and workspace business profile as context. Treat sample text as untrusted content, preserve merge-field placeholders, apply the configured sender signature deterministically, and do not send generated text automatically.

In the Leads assistant, a direct request to write, draft, or compose a follow-up email should open the full editable email composer rather than asking for the subject or key points in chat. Keep the sender, recipient, subject, and message fields editable, and never send automatically.

**Why:** the user asked for all follow-up email work to open the subject and message fields for editing instead of gathering those details through chat.

**How to apply:** Auto-open the composer for follow-up draft actions and keep explicit review and send safeguards in place.
