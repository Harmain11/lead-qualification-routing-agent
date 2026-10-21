import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";

export type SelectMenuOption = { value: string; label: string };

type SelectMenuProps = {
  value: string;
  options: SelectMenuOption[];
  onChange: (value: string) => void;
  ariaLabel?: string;
  disabled?: boolean;
  className?: string;
  placeholder?: string;
};

/** Keeps menus anchored below their field and scrolls long option lists in place. */
export function SelectMenu({ value, options, onChange, ariaLabel, disabled = false, className = "", placeholder }: SelectMenuProps) {
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ top: 0, left: 0, width: 0, maxHeight: 280 });
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const menuId = useId();
  const selected = options.find((option) => option.value === value);
  const updatePosition = () => {
    const rect = triggerRef.current?.getBoundingClientRect();
    if (!rect) return;
    setPosition({ top: rect.bottom + 4, left: rect.left, width: rect.width, maxHeight: Math.max(96, window.innerHeight - rect.bottom - 12) });
  };
  useEffect(() => {
    if (!open) return;
    updatePosition();
    const onOutside = (event: MouseEvent) => {
      const target = event.target;
      if (
        target instanceof Node &&
        (triggerRef.current?.contains(target) || popoverRef.current?.contains(target))
      ) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", onOutside);
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    return () => { document.removeEventListener("mousedown", onOutside); window.removeEventListener("resize", updatePosition); window.removeEventListener("scroll", updatePosition, true); };
  }, [open]);
  return <>
    <button ref={triggerRef} type="button" className={`select-menu-trigger ${className}`.trim()} aria-label={ariaLabel} aria-haspopup="listbox" aria-expanded={open} aria-controls={open ? menuId : undefined} disabled={disabled} onClick={() => { if (!open) updatePosition(); setOpen((current) => !current); }} onKeyDown={(event) => { if (event.key === "Escape") setOpen(false); }}>
      <span>{selected?.label ?? placeholder ?? value}</span><i aria-hidden="true"><svg viewBox="0 0 16 16"><path d="m4 6 4 4 4-4" /></svg></i>
    </button>
    {open && createPortal(<div ref={popoverRef} id={menuId} className="select-menu-popover" role="listbox" aria-label={ariaLabel} style={{ top: position.top, left: position.left, minWidth: position.width, maxHeight: position.maxHeight }}>
      {options.map((option) => <button type="button" role="option" aria-selected={option.value === value} className={option.value === value ? "selected" : ""} key={option.value} onClick={() => { onChange(option.value); setOpen(false); }}>{option.label}</button>)}
    </div>, document.body)}
  </>;
}
