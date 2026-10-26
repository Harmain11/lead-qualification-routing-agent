---
name: Corral Agent mobile layout
description: Responsive interaction constraints for the full-screen Corral Agent on phones and in landscape.
---

On phones, the open Agent is a full-screen surface sized to the visual viewport and safe-area insets. Lock underlying document and body scrolling while it is open, then restore their prior inline styles when it closes or returns to desktop width. Keep the transcript as the only content scroller; the header and composer stay visible as the keyboard reduces height. Maintain 16px text and 44px touch targets, and wrap or truncate long names and messages.

**Why:** The underlying Lead page can exceed a narrow viewport, so its scrollbar and scroll gestures must not leak through the full-screen Agent. Layout-viewport sizing can also extend the panel past visible edges or hide the composer when the keyboard changes the viewport.

**How to apply:** When changing mobile Agent layout or behavior, account for visual-viewport size and offset, preserve safe-area padding, update both Lead and Workspace modes, and verify short-height keyboard layouts plus background-scroll restoration.
