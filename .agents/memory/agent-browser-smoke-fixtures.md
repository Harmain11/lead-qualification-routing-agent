---
name: Agent browser smoke fixtures
description: Keep browser-rendered Agent UI fixtures alive across mode changes.
---

When a browser smoke test mounts a React component into a DOM node inserted into an Agent transcript, verify the fixture still exists before asserting on it. Switching modes can remount the transcript and replace manually inserted children.

**Why:** A fixture can pass when first mounted and then silently disappear when React recreates the mode-specific panel, leaving viewport checks to inspect no fixture at all.

**How to apply:** Mount the fixture after the target mode is active, check for its rendered heading or component before each mode's responsive assertions, and re-mount it if React replaced the transcript contents.
