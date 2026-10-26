---
name: Corral cascade layers
description: How legacy layered styles can block newer shared control treatments.
---

Legacy CSS is grouped into a cascade layer, while v3 styles are unlayered. For `!important` declarations, cascade layer priority reverses, so an older important declaration can beat a newer unlayered important override even when the newer rule appears later.

**Why:** A hard-coded operations-page gradient continued to override shared primary-action hover and pressed colors in the browser, despite the v3 override being later in the source.

**How to apply:** When a shared v3 style does not appear in the browser, inspect important declarations in the legacy layer first. Update or remove redundant legacy rules instead of stacking more specificity in an unlayered stylesheet.
