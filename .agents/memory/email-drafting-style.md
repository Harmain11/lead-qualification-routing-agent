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
