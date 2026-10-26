---
name: Nested flyout alignment
description: Corral category submenus must be distinct from and aligned with their parent selection.
---

Open a nested category submenu beside the main flyout with a visible gap, aligned to the selected category row. Keep the submenu within the viewport and avoid horizontal scrolling or overlap with the parent panel.

**Why:** The user reported that integration submenus opened misaligned and overlapped the main flyout.

**How to apply:** Verify the parent and submenu bounds at narrow desktop widths and preserve visible overflow on the main flyout so the nested panel is not clipped.
