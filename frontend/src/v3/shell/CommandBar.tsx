import { OPEN_SEARCH_EVENT } from "../../components/CommandPalette";
import { Icon, type IconName } from "../ui/Icon";
import { Menu, MenuItem, MenuSeparator } from "../ui/primitives";

/** The search field in the toolbar. It opens the command palette (⌘K). */
export function SearchField() {
  return (
    <button
      type="button"
      className="c-search"
      onClick={() => window.dispatchEvent(new CustomEvent(OPEN_SEARCH_EVENT))}
      aria-keyshortcuts="Control+K Meta+K"
    >
      <Icon name="search" size={15} />
      <span>Search leads, companies or pages</span>
    </button>
  );
}

export type QuickItem = {
  id: string;
  label: string;
  icon: IconName;
  active?: boolean;
  pressed?: boolean;
  count?: number;
  live?: boolean;
  liveLabel?: string;
  onClick: () => void;
  ariaLabel?: string;
};

/** One-click places and tools people reach for all day. */
export function QuickAccess({ items }: { items: QuickItem[] }) {
  return (
    <div className="c-quick" role="group" aria-label="Quick access">
      {items.map((item) => (
        <button
          key={item.id}
          type="button"
          className="c-quick-item"
          data-active={item.active || undefined}
          data-live={item.live || undefined}
          aria-pressed={item.pressed}
          aria-current={item.active ? "page" : undefined}
          aria-label={item.ariaLabel}
          title={item.label}
          onClick={item.onClick}
        >
          <span className="c-quick-icon">
            <Icon name={item.icon} size={16} />
            {item.live && <i className="c-live" aria-hidden="true" />}
          </span>
          <span className="c-quick-label">{item.live && item.liveLabel ? item.liveLabel : item.label}</span>
          {item.count ? <span className="c-quick-count num">{item.count > 99 ? "99+" : item.count}</span> : null}
        </button>
      ))}
    </div>
  );
}

export type NewAction = { id: string; label: string; icon: IconName; hint?: string; onSelect: () => void };

/** “New” — the things people start most, from anywhere. */
export function NewMenu({ actions, extra }: { actions: NewAction[]; extra?: NewAction[] }) {
  if (!actions.length) return null;
  return (
    <Menu
      label="Create"
      triggerKind="filled"
      triggerClassName="c-new"
      trigger={
        <>
          <Icon name="plus" size={16} strokeWidth={2} />
          <span className="c-new-label">New</span>
        </>
      }
    >
      {(close) => (
        <>
          {actions.map((action) => (
            <MenuItem key={action.id} icon={action.icon} onSelect={() => { close(); action.onSelect(); }}>
              {action.label}
              {action.hint && <small className="c-menu-hint">{action.hint}</small>}
            </MenuItem>
          ))}
          {extra && extra.length > 0 && <MenuSeparator />}
          {extra?.map((action) => (
            <MenuItem key={action.id} icon={action.icon} onSelect={() => { close(); action.onSelect(); }}>
              {action.label}
            </MenuItem>
          ))}
        </>
      )}
    </Menu>
  );
}
