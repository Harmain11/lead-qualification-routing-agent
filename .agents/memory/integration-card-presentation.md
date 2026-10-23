---
name: Integration card presentation
description: User-requested consistent compact cards and independent expansion.
---

Non-AI integration cards should have the same compact height and shape. Show a consistent plus/minus control that reveals lower details when opened. Keep account name and connection status visible, and do not stretch neighboring compact cards when another card expands.

Expanded cards must cover both grid columns horizontally and push the remaining
integrations down; keep a single column on phones.

When expanded, keep provider details compact and easy to scan: use tighter spacing,
smaller inner panels, and avoid stacking visually heavy card treatments.

AI providers use one AI Connections entry and one provider-selector card. Keep each provider's credentials, connection status, model settings, and actions separate. Changing the selector chooses which provider to configure; it does not switch the active lead-analysis provider. Preserve the explicit "Use for lead analysis" action.

**Why:** The user explicitly requested that the opened integration cover both boxes
and move the other integrations below it.

**Why:** The user requested that all cards have the same height and shape and show the lower data when clicking a plus or similar control.

**Why:** The user requested one AI connection entry with a provider selector while retaining separate provider connections.

**Why:** On 2026-10-21, the user asked for all expanded integration cards to be smaller and less visually crowded.

**Why:** On 2026-10-21, the user asked for sections inside expanded integration cards to collapse so the cards stay small.

**How to apply:** Preserve compact independent cards for other integrations. In expanded views, reduce excess spacing and nested visual weight while keeping controls readable. Default secondary guidance, health metadata, and advanced settings to collapsed; keep primary connection actions and a short status visible. Keep AI setup and management in one grouped card without merging provider-specific state or changing the active analysis provider by selection alone.
