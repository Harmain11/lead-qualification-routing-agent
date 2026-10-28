import { useEffect } from "react";

/**
 * Gives any remaining native select the same anchored, downward-opening menu
 * behavior as SelectMenu without requiring every form field to be rewritten.
 */
export function NativeSelectEnhancer() {
  useEffect(() => {
    let menu: HTMLDivElement | null = null;
    let source: HTMLSelectElement | null = null;

    const close = () => {
      menu?.remove();
      menu = null;
      source = null;
    };

    const position = () => {
      if (!menu || !source) return;
      const rect = source.getBoundingClientRect();
      menu.style.top = `${rect.bottom + 4}px`;
      menu.style.left = `${rect.left}px`;
      menu.style.minWidth = `${rect.width}px`;
      menu.style.maxHeight = `${Math.max(96, window.innerHeight - rect.bottom - 12)}px`;
    };

    const open = (select: HTMLSelectElement) => {
      close();
      source = select;
      menu = document.createElement("div");
      menu.className = "select-menu-popover native-select-popover";
      menu.setAttribute("role", "listbox");
      menu.setAttribute("aria-label", select.getAttribute("aria-label") || "Choose an option");
      for (const option of Array.from(select.options)) {
        const item = document.createElement("button");
        item.type = "button";
        item.setAttribute("role", "option");
        item.setAttribute("aria-selected", String(option.value === select.value));
        item.className = option.value === select.value ? "selected" : "";
        item.textContent = option.textContent || option.value;
        item.disabled = option.disabled;
        item.addEventListener("click", () => {
          if (!source || option.disabled) return;
          if (source.value !== option.value) {
            source.value = option.value;
            source.dispatchEvent(new Event("change", { bubbles: true }));
          }
          close();
        });
        menu.appendChild(item);
      }
      document.body.appendChild(menu);
      position();
    };

    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Element | null;
      const select = target?.closest("select") as HTMLSelectElement | null;
      if (select) {
        // Native dialogs are rendered in the browser's top layer. A custom
        // menu appended to document.body would sit behind the dialog backdrop
        // and appear to do nothing, so let the browser handle those selects.
        if (select.closest("dialog")) return;
        if (!select.disabled) {
          event.preventDefault();
          event.stopPropagation();
          if (menu && source === select) close();
          else open(select);
        }
        return;
      }
      if (menu && !menu.contains(target)) close();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      const select = event.target instanceof HTMLSelectElement ? event.target : null;
      if (!select || select.disabled) return;
      if (select.closest("dialog")) return;
      if (event.key === "Enter" || event.key === " " || event.key === "ArrowDown") {
        event.preventDefault();
        open(select);
      } else if (event.key === "Escape") {
        close();
      }
    };

    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("resize", position);
    window.addEventListener("scroll", position, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onKeyDown, true);
      window.removeEventListener("resize", position);
      window.removeEventListener("scroll", position, true);
      close();
    };
  }, []);

  return null;
}
