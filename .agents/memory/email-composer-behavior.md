---
name: Email composer behavior
description: Distinguishes collapsing conversation messages from the reply composer in Corral's shared inbox.
---

In Corral's shared inbox, conversation message cards may be collapsed by default, but the reply composer must stay open when the user clicks Reply. Improve the composer's visual hierarchy rather than collapsing it. Provide an X close action and confirm before discarding an unsent draft.

**Why:** the user clarified that collapsing the reply form was not the intended behavior; they wanted a cleaner composer and a direct close action.

**How to apply:** Keep message-card disclosure separate from composing. Open the full form after an explicit Reply action, and protect typed content if the user closes it.
