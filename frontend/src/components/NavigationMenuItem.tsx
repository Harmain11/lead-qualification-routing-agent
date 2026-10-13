import { useEffect, useId, useLayoutEffect, useRef, useState, type FocusEvent, type KeyboardEvent, type ReactNode } from "react";
import { createPortal, flushSync } from "react-dom";

export type NavigationMenuChild = { id: string; label: string };

const integrationChildCategories = [
  ["all", "All"],
  ["email", "Email"],
  ["crm", "CRM"],
  ["ai", "AI"],
  ["calling", "Calling"],
  ["calendar", "Calendar"],
  ["notifications", "Notifications"],
] as const;

function integrationChildCategory(child: NavigationMenuChild) {
  if (["gmail", "outlook"].includes(child.id)) return "email";
  if (["salesforce", "hubspot", "gohighlevel"].includes(child.id)) return "crm";
  if (child.id === "openai") return "ai";
  if (["twilio", "aircall", "ringcentral", "vapi", "retell"].includes(child.id)) return "calling";
  if (["google_calendar", "calendly"].includes(child.id)) return "calendar";
  if (child.id === "slack") return "notifications";
  return "other";
}

export type NavigationMenuItemProps = {
  item: { id: string; label: string; children?: NavigationMenuChild[] };
  icon: ReactNode;
  active: boolean;
  activeChild?: string;
  collapsed?: boolean;
  enabled?: boolean;
  badge?: number;
  onNavigate: (childId?: string) => void;
};

export function NavigationMenuItem({
  item,
  icon,
  active,
  activeChild,
  collapsed = false,
  enabled = true,
  badge,
  onNavigate,
}: NavigationMenuItemProps) {
  const children = item.children ?? [];
  const hasChildren = children.length > 0;
  const menuId = useId();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const flyoutRef = useRef<HTMLDivElement>(null);
  const leaveTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const childRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const escapeDismissal = useRef(false);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [pinnedOpen, setPinnedOpen] = useState(false);
  const [integrationCategory, setIntegrationCategory] = useState("all");
  const [mobile, setMobile] = useState(() => typeof window !== "undefined" && window.matchMedia("(max-width: 760px)").matches);
  const [position, setPosition] = useState<{ left: number; top: number; width: number } | null>(null);
  const open = enabled && hasChildren && (hovered || focused || pinnedOpen);
  const visibleChildren = item.id === "integrations" && integrationCategory !== "all"
    ? children.filter((child) => integrationChildCategory(child) === integrationCategory)
    : children;
  const enterMenu = () => {
    clearTimeout(leaveTimer.current);
    setHovered(true);
  };
  const leaveMenu = () => {
    clearTimeout(leaveTimer.current);
    leaveTimer.current = setTimeout(() => setHovered(false), 140);
  };
  useEffect(() => {
    const media = window.matchMedia("(max-width: 760px)");
    const sync = () => setMobile(media.matches);
    media.addEventListener("change", sync);
    return () => {
      media.removeEventListener("change", sync);
      clearTimeout(leaveTimer.current);
    };
  }, []);
  useEffect(() => {
    if (enabled) return;
    setHovered(false);
    setFocused(false);
    setPinnedOpen(false);
  }, [enabled]);
  useLayoutEffect(() => {
    if (!open || mobile) return;
    const locate = () => {
      const root = rootRef.current;
      if (!root) return;
      const bounds = root.getBoundingClientRect();
      const rail = root.closest(".side-rail, .mini-rail")?.getBoundingClientRect() ?? bounds;
      const width = Math.min(264, window.innerWidth - 24);
      const height = Math.min(flyoutRef.current?.scrollHeight ?? 260, window.innerHeight - 24);
      setPosition({
        left: Math.max(12, Math.min(rail.right + 8, window.innerWidth - width - 12)),
        top: Math.max(12, Math.min(bounds.top, window.innerHeight - height - 12)),
        width,
      });
    };
    locate();
    window.addEventListener("resize", locate);
    document.addEventListener("scroll", locate, true);
    return () => {
      window.removeEventListener("resize", locate);
      document.removeEventListener("scroll", locate, true);
    };
  }, [open, mobile]);
  useEffect(() => {
    if (!open) return;
    const dismiss = (event: PointerEvent) => {
      if (event.target instanceof Node && (rootRef.current?.contains(event.target) || flyoutRef.current?.contains(event.target))) return;
      setPinnedOpen(false);
      setFocused(false);
      setHovered(false);
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [open]);

  const focusChild = (index: number) => {
    if (!visibleChildren.length) return;
    flushSync(() => {
      setHovered(false);
      setPinnedOpen(true);
    });
    childRefs.current[(index + visibleChildren.length) % visibleChildren.length]?.focus();
  };

  const handleBlur = (event: FocusEvent<HTMLDivElement>) => {
    if (event.relatedTarget instanceof Node && (event.currentTarget.contains(event.relatedTarget) || flyoutRef.current?.contains(event.relatedTarget))) return;
    escapeDismissal.current = false;
    setFocused(false);
    setPinnedOpen(false);
  };

  const handleFocus = (event: FocusEvent<HTMLDivElement>) => {
    if ((event.target as Node) === triggerRef.current && escapeDismissal.current) return;
    escapeDismissal.current = false;
    setFocused(true);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape" && open) {
      event.preventDefault();
      event.stopPropagation();
      escapeDismissal.current = true;
      setPinnedOpen(false);
      setHovered(false);
      setFocused(false);
      triggerRef.current?.focus();
      return;
    }
    if (!hasChildren) return;
    const target = event.target as HTMLElement;
    const childIndex = childRefs.current.findIndex((node) => node === target);
    if (childIndex < 0 && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
      event.preventDefault();
      focusChild(event.key === "ArrowDown" ? 0 : children.length - 1);
      return;
    }
    if (childIndex >= 0 && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
      event.preventDefault();
      focusChild(childIndex + (event.key === "ArrowDown" ? 1 : -1));
    }
  };

  const flyout = hasChildren && <div
    ref={flyoutRef}
    id={menuId}
    className={`navigation-menu-flyout${mobile ? " is-inline" : " is-portal"}`}
    data-navigation-owner={item.id}
    data-navigation-mode={collapsed ? "collapsed" : "expanded"}
    aria-label={`${item.label} subcategories`}
    hidden={!open}
    style={!mobile ? { ...position, visibility: position ? undefined : "hidden" } : undefined}
    onMouseEnter={enterMenu}
    onMouseLeave={leaveMenu}
  >
    {item.id === "integrations" && <div className="navigation-menu-categories" role="tablist" aria-label="Integration categories">
      {integrationChildCategories.map(([id, label]) => {
        const count = id === "all" ? children.length : children.filter((child) => integrationChildCategory(child) === id).length;
        if (!count) return null;
        return <button
          type="button"
          role="tab"
          aria-selected={integrationCategory === id}
          className={integrationCategory === id ? "is-active" : ""}
          key={id}
          onClick={() => setIntegrationCategory(id)}
        >{label}<span>{count}</span></button>;
      })}
    </div>}
    {visibleChildren.map((child, index) => <button
      key={child.id}
      ref={(node) => { childRefs.current[index] = node; }}
      type="button"
      className={`navigation-menu-child${activeChild === child.id ? " is-active" : ""}`}
      aria-current={activeChild === child.id ? "page" : undefined}
      onClick={() => {
        setPinnedOpen(false);
        setHovered(false);
        setFocused(false);
        onNavigate(child.id);
      }}
    >
      <span>{child.label}</span>
      {activeChild === child.id && <i aria-hidden="true" />}
    </button>)}
  </div>;
  return (
    <div
      ref={rootRef}
      data-navigation-id={item.id}
      className={`navigation-menu-item${collapsed ? " is-collapsed" : ""}${active ? " is-active" : ""}${open ? " is-open" : ""}`}
      onMouseEnter={enterMenu}
      onMouseLeave={leaveMenu}
      onFocus={handleFocus}
      onBlur={handleBlur}
      onKeyDown={handleKeyDown}
    >
      <button
        ref={triggerRef}
        type="button"
        className="navigation-menu-trigger"
        onClick={() => { setPinnedOpen(false); setFocused(false); setHovered(false); onNavigate(); }}
        aria-current={active ? "page" : undefined}
        aria-label={collapsed ? item.label : undefined}
        title={item.label}
      >
        <i className="navigation-menu-icon">{icon}</i>
        <span className="navigation-menu-label">{item.label}</span>
        {typeof badge === "number" && badge > 0 && <b className="navigation-menu-badge">{badge}</b>}
      </button>
      {hasChildren && (
        <>
          <button
            type="button"
            className="navigation-menu-disclosure"
            aria-label={`${pinnedOpen ? "Close" : "Open"} ${item.label} subcategories`}
            aria-expanded={open}
            aria-controls={menuId}
            onClick={() => {
              if (pinnedOpen) { setFocused(false); setHovered(false); }
              setPinnedOpen(!pinnedOpen);
            }}
            title={`${pinnedOpen ? "Close" : "Open"} ${item.label} subcategories`}
          >
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m5.5 3.5 4.5 4.5-4.5 4.5" /></svg>
          </button>
          {mobile ? flyout : createPortal(flyout, document.body)}
        </>
      )}
    </div>
  );
}

export default NavigationMenuItem;
