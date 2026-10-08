---
name: Browser harness compatibility
description: Distinguish debugger startup failures from app failures when verifying the signed-in UI.
---

CDP support can differ between the shell Chromium wrapper and the native Chromium supplied with installed Playwright browsers.

**Why:** The shell browser repeatedly launched without exposing its debugger endpoint, while the installed native Playwright browser successfully ran the same signed-in checks with an isolated profile.

**How to apply:** When the debugger does not start, investigate the browser harness separately from the app. Discover and select an existing native browser executable; do not assume the app is broken or install another browser unnecessarily.

Viewport changes can complete before React navigation state and CSS transitions settle.

**Why:** An immediate phone screenshot showed a desktop-width sidebar gap even though the finished layout fit the phone correctly.

**How to apply:** Wait for responsive navigation state and transition completion before calculating pointer positions or capturing a resized viewport.

DOM-valued CDP wait expressions must be converted to booleans.

**Why:** By-value serialization can traverse React's internal node references and fail with “Object reference chain is too long,” which is a harness error rather than an application exception.

**How to apply:** Return primitive values from browser evaluations used for polling.

An open dropdown in the DOM does not prove that users can see or click its options.

**Why:** Navigation checks passed while the sidebar's scrolling container clipped its flyout.

**How to apply:** Check option hit targets, viewport bounds, opacity and actual screenshots, especially when a dropdown lives inside a scrolling or transformed container.
