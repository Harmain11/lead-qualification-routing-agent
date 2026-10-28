---
name: Nested flyout alignment
description: Corral category submenus must be distinct from and aligned with their parent selection.
---

Open a nested category submenu beside the main flyout with a visible gap, aligned to the selected category row. Keep the submenu within the viewport and avoid horizontal scrolling or overlap with the parent panel. Clamp short top-level flyouts, such as Settings, using their rendered dimensions rather than a fixed assumed height.

**Why:** The user reported that integration submenus opened misaligned and overlapped the main flyout. A fixed 320px height estimate also placed the short Settings panel far above its row.

**How to apply:** Anchor to the visible navigation row, measure the rendered panel after it opens, recalculate on scroll and resize, and verify menu bounds at narrow desktop widths while preserving visible overflow so nested panels are not clipped.
