---
name: Corral Agent mobile layout
description: Responsive interaction constraints for the full-screen Corral Agent on phones and in landscape.
---

On phones, the open Agent is a full-screen surface sized to the visual viewport and safe-area insets. Keep the transcript as the primary content scroller, but let vertical gestures continue to the underlying page at either transcript edge. Avoid overflow wrappers that block this handoff. The header and composer stay visible as the keyboard reduces height. Maintain 16px text and 44px touch targets, and wrap or truncate long names and messages.

**Why:** The user wants scrolls to continue through the page when a nested Agent or shared navigation scroller reaches its top or bottom. Layout-viewport sizing can also extend the panel past visible edges or hide the composer when the keyboard changes the viewport.

**How to apply:** When changing mobile Agent layout or behavior, account for visual-viewport size and offset, preserve safe-area padding, allow vertical chaining while keeping horizontal containment, update both Lead and Workspace modes, and verify both scroll directions at boundaries.
