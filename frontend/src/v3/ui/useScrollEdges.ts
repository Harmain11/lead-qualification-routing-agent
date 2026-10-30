import { useEffect, type RefObject } from "react";

/**
 * Marks a horizontal scroller with data-start / data-end while there is more
 * content beyond that edge, so CSS can fade only the edges that hide something.
 */
export function useScrollEdges(ref: RefObject<HTMLElement | null>, deps: unknown[] = []) {
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const update = () => {
      const max = element.scrollWidth - element.clientWidth;
      element.toggleAttribute("data-more-start", element.scrollLeft > 2);
      element.toggleAttribute("data-more-end", element.scrollLeft < max - 2);
    };
    update();
    element.addEventListener("scroll", update, { passive: true });
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => {
      element.removeEventListener("scroll", update);
      observer.disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}
