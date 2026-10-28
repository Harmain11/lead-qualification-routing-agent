---
name: Browser harness compatibility
description: Distinguish debugger startup failures from app failures when verifying the signed-in UI.
---

CDP support can differ between the shell Chromium wrapper and a native browser executable.

**Why:** A shell wrapper previously launched without exposing its debugger endpoint. Native browser executables, including the system browser's unwrapped binary, successfully ran signed-in checks with an isolated profile.

**How to apply:** When the debugger does not start, investigate the browser harness separately from the app. Read the existing wrapper's exec target and try that native executable before looking for Playwright browsers or installing another browser.

Native Chromium may list an extension background page before the requested blank tab in its debugger targets.

**Why:** Selecting the first target caused `Page.navigate` to abort on the extension page and left the harness inspecting an empty body while the app itself was healthy.

**How to apply:** Query `/json/list` and select the `page` target whose URL is `about:blank` before navigating.

Viewport changes can complete before React navigation state and CSS transitions settle.

**Why:** An immediate phone screenshot showed a desktop-width sidebar gap even though the finished layout fit the phone correctly.

**How to apply:** Wait for responsive navigation state and transition completion before calculating pointer positions or capturing a resized viewport.

DOM-valued CDP wait expressions must be converted to booleans.

**Why:** By-value serialization can traverse React's internal node references and fail with “Object reference chain is too long,” which is a harness error rather than an application exception.

**How to apply:** Return primitive values from browser evaluations used for polling.

Programmatic focus does not reliably reproduce React hover-only menus in the browser smoke harness.

**Why:** A focus-based flyout check failed to open the actual pointer-driven menu, while dispatching a bubbling `mouseover` event exercised the intended handler.

**How to apply:** Use a bubbling mouse event or real CDP pointer movement when validating hover-driven menus; then assert the rendered bounds and hit area.

An open dropdown in the DOM does not prove that users can see or click its options.

**Why:** Navigation checks passed while the sidebar's scrolling container clipped its flyout.

**How to apply:** Check option hit targets, viewport bounds, opacity and actual screenshots, especially when a dropdown lives inside a scrolling or transformed container.
