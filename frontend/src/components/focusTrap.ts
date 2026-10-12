import type { KeyboardEvent } from "react";

export function trapFocus(event: KeyboardEvent<HTMLElement>) {
  if (event.key !== "Tab") return;
  const element = event.currentTarget;
  const controls = Array.from(element.querySelectorAll<HTMLElement>(
    'button:not(:disabled), a[href], input:not(:disabled):not([type="hidden"]), select:not(:disabled), textarea:not(:disabled), summary, [tabindex]:not([tabindex="-1"])',
  )).filter((control) => control.tabIndex >= 0 && control.getClientRects().length > 0);
  const first = controls[0];
  const last = controls.at(-1);
  if (!first) {
    event.preventDefault();
    element.focus();
  } else if (event.shiftKey && (document.activeElement === first || !element.contains(document.activeElement))) {
    event.preventDefault();
    last?.focus();
  } else if (!event.shiftKey && (document.activeElement === last || !element.contains(document.activeElement))) {
    event.preventDefault();
    first.focus();
  }
}
