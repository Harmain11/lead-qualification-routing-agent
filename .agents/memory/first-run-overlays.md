---
name: First-run dialog priority
description: Sequencing the product tour and optional OpenAI setup dialog.
---

The ProductTour takes priority over the optional OpenAI setup prompt. Keep the OpenAI prompt queued while the tour is open, and show it only on the dashboard after the tour closes. Never render both first-run dialogs at once.

**Why:** The independent first-run conditions opened both dialogs together, causing their centered content to overlap and obscure each other.

**How to apply:** If another startup dialog is added, coordinate it through the same single-dialog priority or defer it until the existing tour/modal state closes.
