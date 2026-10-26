import {
  useEffect,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type ReactNode,
} from "react";
import { trapFocus } from "../../components/focusTrap";
import { Icon, type IconName } from "./Icon";

/* ------------------------------------------------------------------ Button */

type ButtonKind = "filled" | "tinted" | "gray" | "plain" | "destructive";
type ButtonSize = "sm" | "md" | "lg";

export function Button({
  kind = "gray",
  size = "md",
  icon,
  trailingIcon,
  children,
  className,
  type = "button",
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  kind?: ButtonKind;
  size?: ButtonSize;
  icon?: IconName;
  trailingIcon?: IconName;
}) {
  return (
    <button
      type={type}
      className={`v-btn${className ? ` ${className}` : ""}`}
      data-kind={kind}
      data-size={size}
      {...rest}
    >
      {icon && <Icon name={icon} size={size === "sm" ? 15 : 17} />}
      {children != null && <span className="v-btn-label">{children}</span>}
      {trailingIcon && <Icon name={trailingIcon} size={14} className="v-btn-trailing" />}
    </button>
  );
}

export function IconButton({
  icon,
  label,
  className,
  size = 18,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { icon: IconName; label: string; size?: number }) {
  return (
    <button type="button" className={`v-iconbtn${className ? ` ${className}` : ""}`} aria-label={label} title={label} {...rest}>
      <Icon name={icon} size={size} />
    </button>
  );
}

/* --------------------------------------------------------------- Segmented */

export function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
  size = "md",
}: {
  label: string;
  value: T;
  options: Array<{ value: T; label: string }>;
  onChange: (value: T) => void;
  size?: "sm" | "md";
}) {
  const index = Math.max(0, options.findIndex((option) => option.value === value));
  return (
    <div
      className="v-seg"
      role="radiogroup"
      aria-label={label}
      data-size={size}
      style={{ "--seg-count": options.length, "--seg-index": index } as React.CSSProperties}
    >
      <span className="v-seg-thumb" aria-hidden="true" />
      {options.map((option, position) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={option.value === value}
          tabIndex={option.value === value ? 0 : -1}
          onClick={() => onChange(option.value)}
          onKeyDown={(event) => {
            const delta = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
            if (!delta) return;
            event.preventDefault();
            const next = options[(position + delta + options.length) % options.length];
            onChange(next.value);
            (event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>("[role=radio]")[
              (position + delta + options.length) % options.length
            ])?.focus();
          }}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------- Badge */

export type Tone = "neutral" | "tint" | "strong" | "attention" | "danger" | "info";

export function Badge({ tone = "neutral", children, dot }: { tone?: Tone; children: ReactNode; dot?: boolean }) {
  return (
    <span className="v-badge" data-tone={tone}>
      {dot && <i aria-hidden="true" />}
      {children}
    </span>
  );
}

const BAND_LABELS: Record<string, string> = {
  hot: "Hot",
  warm: "Warm",
  needs_qualifying: "Needs qualifying",
  nurture: "Nurture",
  disqualify: "Disqualified",
  disqualified: "Disqualified",
  hard_disqualified: "Disqualified",
  pending: "Not scored",
};

export function bandLabel(band: string | null | undefined) {
  if (!band) return "Not scored";
  return BAND_LABELS[band] ?? band.replaceAll("_", " ").replace(/^\w/, (letter) => letter.toUpperCase());
}

export function bandTone(band: string | null | undefined, review = false): Tone {
  if (review) return "attention";
  switch (band) {
    case "hot": return "strong";
    case "warm": return "tint";
    case "needs_qualifying": return "info";
    case "disqualify":
    case "disqualified":
    case "hard_disqualified": return "danger";
    default: return "neutral";
  }
}

export function BandBadge({ band, review = false }: { band: string | null | undefined; review?: boolean }) {
  return <Badge tone={bandTone(band, review)}>{review ? "Review" : bandLabel(band)}</Badge>;
}

/* --------------------------------------------------------------- ScoreRing */

/**
 * A qualification score drawn as a single ring. The arc is the score out of 100;
 * colour follows the band (amber only when a person must review it).
 */
export function ScoreRing({
  score,
  band,
  review = false,
  size = 44,
  label,
}: {
  score: number | null | undefined;
  band?: string | null;
  review?: boolean;
  size?: number;
  label?: string;
}) {
  const stroke = size >= 64 ? 6 : size >= 40 ? 4 : 3;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const value = typeof score === "number" ? Math.max(0, Math.min(100, score)) : 0;
  const tone = bandTone(band, review);
  return (
    <span
      className="v-ring"
      data-tone={tone}
      style={{ width: size, height: size }}
      role={label === "" ? undefined : "img"}
      aria-hidden={label === "" ? true : undefined}
      aria-label={label === "" ? undefined : label ?? (typeof score === "number" ? `Score ${score} of 100` : "Not scored")}
    >
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
        <circle className="v-ring-track" cx={size / 2} cy={size / 2} r={radius} strokeWidth={stroke} />
        {typeof score === "number" && (
          <circle
            className="v-ring-value"
            cx={size / 2}
            cy={size / 2}
            r={radius}
            strokeWidth={stroke}
            strokeDasharray={circumference}
            strokeDashoffset={circumference * (1 - value / 100)}
          />
        )}
      </svg>
      <b className="num" style={{ fontSize: Math.round(size * (size >= 64 ? 0.32 : 0.34)) }}>
        {typeof score === "number" ? score : "–"}
      </b>
    </span>
  );
}

/* -------------------------------------------------------------------- Menu */

export function Menu({
  label,
  trigger,
  children,
  align = "end",
  triggerKind = "gray",
  triggerClassName,
  iconOnly,
}: {
  label: string;
  trigger: ReactNode;
  children: (close: () => void) => ReactNode;
  align?: "start" | "end" | "up";
  triggerKind?: ButtonKind;
  triggerClassName?: string;
  iconOnly?: IconName;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuId = useId();
  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        const items = [...(rootRef.current?.querySelectorAll<HTMLElement>("[role=menuitem]:not(:disabled)") ?? [])];
        if (!items.length) return;
        event.preventDefault();
        const index = items.indexOf(document.activeElement as HTMLElement);
        const next = event.key === "ArrowDown" ? (index + 1) % items.length : (index - 1 + items.length) % items.length;
        items[next]?.focus();
      }
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    rootRef.current?.querySelector<HTMLElement>("[role=menuitem]")?.focus();
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  const close = () => {
    setOpen(false);
    buttonRef.current?.focus();
  };
  return (
    <div className="v-menu-root" ref={rootRef}>
      <button
        ref={buttonRef}
        type="button"
        className={`${iconOnly ? "v-iconbtn" : "v-btn"}${triggerClassName ? ` ${triggerClassName}` : ""}`}
        data-kind={iconOnly ? undefined : triggerKind}
        data-size="md"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={iconOnly ? label : undefined}
        title={iconOnly ? label : undefined}
        onClick={() => setOpen((current) => !current)}
      >
        {iconOnly ? <Icon name={iconOnly} /> : trigger}
      </button>
      {open && (
        <div className="v-menu" id={menuId} role="menu" aria-label={label} data-align={align}>
          {children(close)}
        </div>
      )}
    </div>
  );
}

export function MenuItem({
  children,
  onSelect,
  icon,
  danger,
  disabled,
  ...rest
}: {
  children: ReactNode;
  onSelect: () => void;
  icon?: IconName;
  danger?: boolean;
  disabled?: boolean;
} & Omit<ButtonHTMLAttributes<HTMLButtonElement>, "onSelect">) {
  return (
    <button type="button" role="menuitem" className="v-menu-item" data-danger={danger || undefined} disabled={disabled} onClick={onSelect} {...rest}>
      {icon ? <Icon name={icon} size={16} /> : <span className="v-menu-gap" aria-hidden="true" />}
      <span>{children}</span>
    </button>
  );
}

export function MenuSeparator() {
  return <span className="v-menu-sep" role="separator" />;
}

/* ------------------------------------------------------------------- Sheet */

/**
 * A modal sheet. On large screens it floats centred; on phones it rises from
 * the bottom edge. Escape and the close button dismiss it; focus is trapped.
 */
export function Sheet({
  title,
  subtitle,
  onClose,
  children,
  footer,
  width = 560,
  dismissible = true,
}: {
  title: string;
  subtitle?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  width?: number;
  dismissible?: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const element = dialog.current;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    element?.showModal();
    element?.querySelector<HTMLElement>("[autofocus], input:not([type=radio]):not([type=checkbox]), textarea, select")?.focus();
    return () => {
      element?.close();
      if (previous?.isConnected) previous.focus();
    };
  }, []);
  return (
    <dialog
      ref={dialog}
      className="v-sheet"
      aria-labelledby={titleId}
      style={{ "--sheet-w": `${width}px` } as React.CSSProperties}
      onKeyDown={trapFocus}
      onCancel={(event) => {
        event.preventDefault();
        if (dismissible) onClose();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget && dismissible) onClose();
      }}
    >
      <div className="v-sheet-body">
        <header className="v-sheet-head">
          <div>
            <h2 id={titleId}>{title}</h2>
            {subtitle && <p>{subtitle}</p>}
          </div>
          <IconButton icon="close" label={`Close ${title}`} onClick={onClose} disabled={!dismissible} className="v-sheet-close" size={16} />
        </header>
        <div className="v-sheet-content">{children}</div>
        {footer && <footer className="v-sheet-foot">{footer}</footer>}
      </div>
    </dialog>
  );
}

/* --------------------------------------------------------- Grouped lists */

export function Group({ title, footer, children, action }: { title?: ReactNode; footer?: ReactNode; children: ReactNode; action?: ReactNode }) {
  return (
    <section className="v-group">
      {(title || action) && (
        <header className="v-group-head">
          {title && <h3>{title}</h3>}
          {action}
        </header>
      )}
      <div className="v-group-body">{children}</div>
      {footer && <p className="v-group-foot">{footer}</p>}
    </section>
  );
}

export function Row({ label, children, icon, detail }: { label: ReactNode; children?: ReactNode; icon?: IconName; detail?: ReactNode }) {
  return (
    <div className="v-row">
      {icon && <span className="v-row-icon"><Icon name={icon} size={16} /></span>}
      <span className="v-row-label">{label}</span>
      <span className="v-row-value">{children}</span>
      {detail && <span className="v-row-detail">{detail}</span>}
    </div>
  );
}

/* ------------------------------------------------------------- Feedback */

export function Banner({
  tone = "neutral",
  title,
  children,
  action,
  role,
}: {
  tone?: Tone;
  title?: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
  role?: "status" | "alert";
}) {
  return (
    <div className="v-banner" data-tone={tone} role={role ?? (tone === "danger" ? "alert" : "status")}>
      <div>
        {title && <b>{title}</b>}
        {children && <span>{children}</span>}
      </div>
      {action}
    </div>
  );
}

export function EmptyState({ icon = "info", title, children, action }: { icon?: IconName; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="v-empty">
      <span className="v-empty-icon"><Icon name={icon} size={22} /></span>
      <b>{title}</b>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

export function Monogram({ text, size = 36 }: { text: string; size?: number }) {
  const letters = text
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => word[0]?.toUpperCase() ?? "")
    .join("") || "?";
  let hash = 0;
  for (const char of text) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return (
    <span className="v-mono" data-hue={hash % 5} style={{ width: size, height: size, fontSize: Math.round(size * 0.38) }} aria-hidden="true">
      {letters}
    </span>
  );
}

export function PageHeader({
  title,
  subtitle,
  actions,
  children,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <header className="v-pagehead">
      <div className="v-pagehead-text">
        <h1>{title}</h1>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {actions && <div className="v-pagehead-actions">{actions}</div>}
      {children}
    </header>
  );
}

/* ----------------------------------------------------------- Formatting */

export const formatShortDate = (value: string | Date) => {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  const now = new Date();
  const sameDay = date.toDateString() === now.toDateString();
  if (sameDay) return date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (date.toDateString() === yesterday.toDateString()) return "Yesterday";
  return date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    ...(date.getFullYear() !== now.getFullYear() ? { year: "numeric" } : {}),
  });
};

export const titleize = (value: string | null | undefined) =>
  value ? value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()) : "";

export const money = (value: number, currency = "USD") =>
  new Intl.NumberFormat(undefined, { style: "currency", currency, maximumFractionDigits: 0 }).format(value);
