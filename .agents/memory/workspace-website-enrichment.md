---
name: Workspace website enrichment
description: Safety and review rules for suggesting workspace business-profile details from a supplied website.
---

Website analysis is an explicit, admin-triggered action. Use the existing bounded public-HTTPS page fetch and the selected workspace AI provider; return concise, source-backed suggestions in the existing business-profile fields. Fill only empty fields, keep the profile unsaved until the user reviews and saves it, and state that only the supplied page was read. The Products and services field accepts a standard preset dropdown that appends to its editable one-per-line list. Never infer internal email domains from the website hostname: suggest them only when an organization contact email is explicitly present in cited page text.

**Why:** The user wants website services and organization details suggested in the existing profile format, concise copy, and standard product/service presets; the profile influences lead detection and manual values must not be silently replaced.

**How to apply:** Reuse the public-page safety checks, validate exact source excerpts, preserve entered values, keep AI output short, and let preset selections append without removing the editable text field. Keep source evidence available for review before the existing save action.
