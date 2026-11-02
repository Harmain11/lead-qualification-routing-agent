import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { leadDetailSections, leadSectionHref } from "../../leadSections";
import type { PinnedLeadSection } from "../../components/PinnedLeadNavigation";
import { OPEN_SEARCH_EVENT } from "../../components/CommandPalette";
import { Icon, type IconName } from "../ui/Icon";
import type { Theme, ThemePreference } from "../../theme";
import { Menu, MenuItem, MenuSeparator, Sheet } from "../ui/primitives";

export type NavChild = { id: string; label: string; children?: NavChild[] };
export type NavEntry = {
  id: string;
  label: string;
  icon: IconName;
  active: boolean;
  count?: number;
  countTone?: "attention" | "tint";
  countLabel?: string;
  /** Sections reachable from a hover menu beside the item. */
  children?: NavChild[];
};
export type NavSection = { id: string; label?: string; items: NavEntry[] };

export type AccountProps = {
  name: string;
  email: string;
  online: boolean;
  theme: Theme;
  themePreference: ThemePreference;
  onSetThemePreference: (preference: ThemePreference) => void;
  onGuide: () => void;
  onSignOut: () => void;
};

const APPEARANCES: Array<{ id: ThemePreference; label: string; icon: IconName }> = [
  { id: "system", label: "System", icon: "monitor" },
  { id: "light", label: "Light", icon: "sun" },
  { id: "dark", label: "Dark", icon: "moon" },
];

const isPlainClick = (event: React.MouseEvent) =>
  event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;

function navHref(id: string) {
  const url = new URL(window.location.href);
  url.search = "";
  url.searchParams.set("view", id);
  return `${url.pathname}${url.search}`;
}

/* ================================================================ Sidebar */

export function Sidebar({
  sections,
  workspace,
  pins,
  pinNotice,
  activePin,
  account,
  onNavigate,
  onOpenPin,
  onRemovePin,
}: {
  sections: NavSection[];
  workspace: ReactNode;
  pins: PinnedLeadSection[];
  pinNotice: string | null;
  activePin?: { leadId?: string; section: string };
  account: AccountProps;
  onNavigate: (id: string, section?: string) => void;
  onOpenPin: (pin: PinnedLeadSection) => void;
  onRemovePin: (pin: PinnedLeadSection) => void;
}) {
  const [flyout, setFlyout] = useState<{ id: string; top: number; left: number } | null>(null);
  const [submenu, setSubmenu] = useState<string | null>(null);
  const closeTimer = useRef(0);
  const sidebarRef = useRef<HTMLElement>(null);
  const flyoutRef = useRef<HTMLDivElement>(null);
  const flyoutAnchorRef = useRef<HTMLElement | null>(null);
  useEffect(() => {
    const closeOutside = (event: Event) => {
      if (!sidebarRef.current?.contains(event.target as Node)) { setFlyout(null); setSubmenu(null); }
    };
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("focusin", closeOutside);
    return () => {
      window.clearTimeout(closeTimer.current);
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("focusin", closeOutside);
    };
  }, []);
  const openFlyout = (id: string, element: HTMLElement) => {
    window.clearTimeout(closeTimer.current);
    const row = element.matches(".v-navitem")
      ? element
      : element.querySelector<HTMLElement>(".v-navitem");
    const anchor = row ?? element;
    flyoutAnchorRef.current = anchor;
    const rect = anchor.getBoundingClientRect();
    const sidebarRight = sidebarRef.current?.getBoundingClientRect().right ?? rect.right;
    setFlyout({
      id,
      top: Math.max(12, rect.top - 6),
      left: Math.max(12, sidebarRight + 8),
    });
    setSubmenu(null);
  };
  const scheduleClose = () => {
    window.clearTimeout(closeTimer.current);
    closeTimer.current = window.setTimeout(() => setFlyout(null), 450);
  };
  const flyoutItem = sections.flatMap((section) => section.items).find((item) => item.id === flyout?.id);
  useLayoutEffect(() => {
    if (!flyout) return;
    const positionFlyout = () => {
      const anchor = flyoutAnchorRef.current;
      const panel = flyoutRef.current;
      if (!anchor || !panel) return;

      const sidebarRight = sidebarRef.current?.getBoundingClientRect().right ?? anchor.getBoundingClientRect().right;
      const panelHeight = panel.offsetHeight;
      const panelWidth = panel.offsetWidth;
      const top = Math.max(
        12,
        Math.min(anchor.getBoundingClientRect().top - 6, window.innerHeight - panelHeight - 12),
      );
      const left = Math.max(
        12,
        Math.min(sidebarRight + 8, window.innerWidth - panelWidth - 12),
      );

      setFlyout((current) => {
        if (!current || current.id !== flyout.id) return current;
        if (Math.abs(current.top - top) < 0.5 && Math.abs(current.left - left) < 0.5) return current;
        return { ...current, top, left };
      });
    };

    positionFlyout();
    window.addEventListener("resize", positionFlyout);
    document.addEventListener("scroll", positionFlyout, true);
    return () => {
      window.removeEventListener("resize", positionFlyout);
      document.removeEventListener("scroll", positionFlyout, true);
    };
  }, [flyout?.id, flyoutItem?.children?.length, submenu]);
  return (
    <aside ref={sidebarRef} className="v-sidebar" aria-label="Corral">
      <div className="v-sidebar-top">
        <div className="v-sidebar-workspace">{workspace}</div>
        <button
          type="button"
          className="v-sidebar-search"
          aria-label="Search Corral"
          onClick={() => window.dispatchEvent(new CustomEvent(OPEN_SEARCH_EVENT))}
          aria-keyshortcuts="Control+K Meta+K"
        >
          <Icon name="search" size={16} />
          <span>Search</span>
        </button>
      </div>
      <nav className="v-sidebar-nav" aria-label="Primary">
        {sections.map((section) => (
          <div className="v-navsection" key={section.id}>
            {section.label && <h2 className="v-navsection-title">{section.label}</h2>}
            <ul>
              {section.items.map((item) => (
                <li
                  key={item.id}
                  onMouseEnter={item.children?.length ? (event) => openFlyout(item.id, event.currentTarget) : () => setFlyout(null)}
                  onMouseLeave={item.children?.length ? scheduleClose : undefined}
                >
                  <a
                    href={navHref(item.id)}
                    className="v-navitem"
                    aria-current={item.active ? "page" : undefined}
                    aria-label={item.label}
                    title={item.label}
                    onFocus={item.children?.length ? (event) => openFlyout(item.id, event.currentTarget) : undefined}
                    onKeyDown={(event) => {
                      if (event.key === "Escape") { setFlyout(null); setSubmenu(null); }
                    }}
                    onClick={(event) => {
                      if (!isPlainClick(event)) return;
                      event.preventDefault();
                      setFlyout(null); setSubmenu(null);
                      onNavigate(item.id);
                    }}
                  >
                    <Icon name={item.icon} size={18} />
                    <span className="v-navitem-label">{item.label}</span>
                    {item.children?.length ? <Icon name="chevronRight" size={12} className="v-navitem-more" /> : null}
                    {item.count ? (
                      <span className="v-count" data-tone={item.countTone} aria-label={item.countLabel}>
                        {item.count > 99 ? "99+" : item.count}
                      </span>
                    ) : null}
                  </a>
                </li>
              ))}
            </ul>
          </div>
        ))}
        {(pins.length > 0 || pinNotice) && (
          <div className="v-navsection">
            <h2 className="v-navsection-title">Pinned</h2>
            <ul>
              {pins.map((pin) => {
                const label = leadDetailSections.find((section) => section.id === pin.section)?.label ?? "";
                const active = activePin?.leadId === pin.leadId && activePin.section === pin.section;
                return (
                  <li key={`${pin.leadId}:${pin.section}`} className="v-pin">
                    <a
                      href={leadSectionHref(pin.leadId, pin.section)}
                      className="v-navitem"
                      aria-current={active ? "page" : undefined}
                      onClick={(event) => {
                        if (!isPlainClick(event)) return;
                        event.preventDefault();
                        onOpenPin(pin);
                      }}
                    >
                      <Icon name="pin" size={16} />
                      <span className="v-navitem-label">
                        {pin.company}
                        <small>{label}</small>
                      </span>
                    </a>
                    <button type="button" className="v-pin-remove" onClick={() => onRemovePin(pin)} aria-label={`Unpin ${[pin.company, label].filter(Boolean).join(" · ")}`}>
                      <Icon name="close" size={12} />
                    </button>
                  </li>
                );
              })}
            </ul>
            {pinNotice && <p className="v-pin-notice" role="status">{pinNotice}</p>}
          </div>
        )}
      </nav>
      {flyoutItem?.children?.length ? (
        <div
          ref={flyoutRef}
          className="v-flyout"
          data-edge={flyout!.top > window.innerHeight / 2 ? "bottom" : "top"}
          role="menu"
          aria-label={`${flyoutItem.label} sections`}
          style={{ top: flyout!.top, left: flyout!.left }}
          onMouseEnter={() => window.clearTimeout(closeTimer.current)}
          onMouseLeave={scheduleClose}
        >
          <span className="v-flyout-title">{flyoutItem.label}</span>
          {flyoutItem.children.map((child) => child.children?.length ? (
            <div className="v-flyout-group" key={child.id}>
              <button type="button" role="menuitem" className="v-flyout-item" aria-haspopup="menu"
                aria-expanded={submenu === child.id} onClick={() => setSubmenu(submenu === child.id ? null : child.id)}
                onKeyDown={(event) => { if (event.key === "Escape" || event.key === "ArrowLeft") setSubmenu(null); }}>
                {child.label}<Icon name="chevronRight" size={12} />
              </button>
              <div className="v-flyout-submenu" role="menu" aria-label={child.label}>
                {child.children.map((provider) => <button key={provider.id} type="button" role="menuitem" className="v-flyout-item"
                  onClick={() => { setFlyout(null); setSubmenu(null); onNavigate(flyoutItem.id, provider.id); }}>{provider.label}</button>)}
              </div>
            </div>
          ) : (
            <button
              key={child.id}
              type="button"
              role="menuitem"
              className="v-flyout-item"
              onClick={() => {
                setFlyout(null);
                onNavigate(flyoutItem.id, child.id);
              }}
            >
              {child.label}
            </button>
          ))}
        </div>
      ) : null}
      <div className="v-sidebar-foot">
        <AccountMenu {...account} />
      </div>
    </aside>
  );
}

function AccountMenu({ name, email, online, themePreference, onSetThemePreference, onGuide, onSignOut }: AccountProps) {
  return (
    <Menu
      label="Account and preferences"
      align="up"
      triggerKind="plain"
      triggerClassName="v-account"
      trigger={
        <>
          <span className="v-avatar" aria-hidden="true">{(name || email || "?").trim().slice(0, 1).toUpperCase()}</span>
          <span className="v-account-text">
            <b>{name || email}</b>
            <small data-online={online || undefined}>{online ? "Connected" : "Offline"}</small>
          </span>
          <Icon name="chevronUpDown" size={14} />
        </>
      }
    >
      {(close) => (
        <>
          <span className="v-menu-label">{email}</span>
          <span className="v-menu-label">Appearance</span>
          {APPEARANCES.map((option) => (
            <MenuItem
              key={option.id}
              icon={themePreference === option.id ? "check" : option.icon}
              role="menuitemradio"
              aria-checked={themePreference === option.id}
              onSelect={() => { onSetThemePreference(option.id); close(); }}
            >
              {option.label}
            </MenuItem>
          ))}
          <MenuSeparator />
          <MenuItem icon="guide" aria-haspopup="dialog" onSelect={() => { close(); onGuide(); }}>Product guide</MenuItem>
          <MenuSeparator />
          <MenuItem icon="signOut" onSelect={() => { close(); onSignOut(); }}>Sign out</MenuItem>
        </>
      )}
    </Menu>
  );
}

/* ============================================================== Tab bar */

/**
 * Phones get a tab bar: the four places people go most, plus everything else
 * one tap away in "More". Reachable with a thumb; never hidden behind a menu.
 */
export function TabBar({
  primary,
  rest,
  workspace,
  account,
  onNavigate,
}: {
  primary: NavEntry[];
  rest: NavSection[];
  workspace: ReactNode;
  account: AccountProps;
  onNavigate: (id: string) => void;
}) {
  const [moreOpen, setMoreOpen] = useState(false);
  const moreActive = rest.some((section) => section.items.some((item) => item.active));
  return (
    <>
      <nav className="v-tabbar" aria-label="Primary">
        {primary.map((item) => (
          <a
            key={item.id}
            href={navHref(item.id)}
            className="v-tab"
            aria-current={item.active ? "page" : undefined}
            onClick={(event) => {
              if (!isPlainClick(event)) return;
              event.preventDefault();
              onNavigate(item.id);
            }}
          >
            <span className="v-tab-icon">
              <Icon name={item.icon} size={22} />
              {item.count ? <i className="v-tab-badge" data-tone={item.countTone} aria-label={item.countLabel}>{item.count > 99 ? "99+" : item.count}</i> : null}
            </span>
            <span>{item.label}</span>
          </a>
        ))}
        <button type="button" className="v-tab" aria-current={moreActive ? "page" : undefined} aria-haspopup="dialog" onClick={() => setMoreOpen(true)}>
          <span className="v-tab-icon"><Icon name="more" size={22} /></span>
          <span>More</span>
        </button>
      </nav>
      {moreOpen && (
        <Sheet title="More" onClose={() => setMoreOpen(false)} width={480}>
          <div className="v-more">
            <div className="v-more-workspace">{workspace}</div>
            {rest.map((section) => (
              <div key={section.id} className="v-more-section">
                {section.label && <h3>{section.label}</h3>}
                <div className="v-group-body">
                  {section.items.map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      className="v-more-item"
                      aria-current={item.active ? "page" : undefined}
                      onClick={() => {
                        setMoreOpen(false);
                        onNavigate(item.id);
                      }}
                    >
                      <span className="v-row-icon"><Icon name={item.icon} size={16} /></span>
                      <span>{item.label}</span>
                      {item.count ? <span className="v-count" data-tone={item.countTone}>{item.count}</span> : null}
                      <Icon name="chevronRight" size={14} className="v-more-chevron" />
                    </button>
                  ))}
                </div>
              </div>
            ))}
            <div className="v-more-section">
              <h3>Account</h3>
              <div className="v-group-body">
                <div className="v-more-item is-static">
                  <span className="v-avatar" aria-hidden="true">{(account.name || account.email || "?").slice(0, 1).toUpperCase()}</span>
                  <span>{account.name || account.email}<small>{account.email}</small></span>
                </div>
                <div className="v-more-item is-static" role="radiogroup" aria-label="Appearance">
                  <span className="v-row-icon"><Icon name={account.theme === "light" ? "sun" : "moon"} size={16} /></span>
                  <span>Appearance</span>
                  <span className="v-appearance-options">
                    {APPEARANCES.map((option) => (
                      <button
                        key={option.id}
                        type="button"
                        role="radio"
                        aria-checked={account.themePreference === option.id}
                        className="v-appearance-option"
                        onClick={() => account.onSetThemePreference(option.id)}
                      >
                        {option.label}
                      </button>
                    ))}
                  </span>
                </div>
                <button type="button" className="v-more-item" onClick={() => { setMoreOpen(false); account.onGuide(); }}>
                  <span className="v-row-icon"><Icon name="guide" size={16} /></span>
                  <span>Product guide</span>
                </button>
                <button type="button" className="v-more-item is-danger" onClick={() => { setMoreOpen(false); account.onSignOut(); }}>
                  <span className="v-row-icon"><Icon name="signOut" size={16} /></span>
                  <span>Sign out</span>
                </button>
              </div>
            </div>
          </div>
        </Sheet>
      )}
    </>
  );
}

/* ============================================================== Toolbar */

/**
 * The window toolbar. It stays quiet: the page's own large title introduces the
 * page, and the toolbar only picks the title up once that heading scrolls away.
 */
export function Toolbar({
  title,
  leading,
  trailing,
  scrollRoot,
}: {
  title: string;
  leading?: ReactNode;
  trailing?: ReactNode;
  scrollRoot?: React.RefObject<HTMLElement | null>;
}) {
  const [scrolled, setScrolled] = useState(false);
  const raf = useRef(0);
  useEffect(() => {
    const target: HTMLElement | Window = scrollRoot?.current ?? window;
    const read = () => {
      cancelAnimationFrame(raf.current);
      raf.current = requestAnimationFrame(() => {
        const top = target instanceof Window ? target.scrollY : target.scrollTop;
        setScrolled(top > 28);
      });
    };
    read();
    target.addEventListener("scroll", read, { passive: true });
    return () => {
      cancelAnimationFrame(raf.current);
      target.removeEventListener("scroll", read);
    };
  }, [scrollRoot]);
  return (
    <header className="v-toolbar" data-scrolled={scrolled || undefined}>
      <div className="v-toolbar-leading">{leading}</div>
      <div className="v-toolbar-title" aria-hidden={!scrolled}>{title}</div>
      <div className="v-toolbar-trailing">{trailing}</div>
    </header>
  );
}
