import type { CSSProperties, ReactNode } from "react";
import { memo, useState } from "react";
import { Button, Sheet } from "../v3/ui/primitives";
import type {
  AdvancedAnalytics,
  AgentActivity,
  ChannelAnalytics,
  DashboardAnalytics,
  ExtendedAnalytics,
  ManualReviewAnalytics,
  PipelineAnalytics,
  RepAnalytics,
  RevenueAnalytics,
  TimeSeriesAnalytics,
} from "../types";

export type AnalyticsBundle = {
  dashboard: DashboardAnalytics | null;
  channels: ChannelAnalytics | null;
  pipeline: PipelineAnalytics | null;
  revenue: RevenueAnalytics | null;
  reps: RepAnalytics | null;
  series: TimeSeriesAnalytics | null;
  agents: AgentActivity | null;
  review: ManualReviewAnalytics | null;
  extended: ExtendedAnalytics | null;
  advanced: AdvancedAnalytics | null;
};

export type VisibilityOption<T extends string> = {
  id: T;
  label: string;
  group?: string;
};

export function defaultSectionSelection<T extends string>(
  options: readonly { id: T }[],
) {
  return new Set<T>(options.map((option) => option.id));
}

export function loadSectionSelection<T extends string>(
  storageKey: string,
  options: readonly { id: T }[],
) {
  const defaults = defaultSectionSelection(options);
  if (typeof window === "undefined") return defaults;
  try {
    const saved = window.localStorage.getItem(storageKey);
    if (!saved) return defaults;
    const parsed: unknown = JSON.parse(saved);
    if (!Array.isArray(parsed)) return defaults;
    const validIds = new Set<string>(options.map((option) => option.id));
    return new Set(
      parsed.filter(
        (value): value is T => typeof value === "string" && validIds.has(value),
      ),
    );
  } catch {
    return defaults;
  }
}

export function SectionVisibilityPicker<T extends string>({
  options,
  visible,
  applied,
  description,
  showAllLabel,
  onToggle,
  onShowAll,
  onSubmit,
}: {
  options: readonly VisibilityOption<T>[];
  visible: ReadonlySet<T>;
  applied: ReadonlySet<T>;
  description: string;
  showAllLabel: string;
  onToggle: (id: T, checked: boolean) => void;
  onShowAll: () => void;
  onSubmit: () => void;
}) {
  const [open, setOpen] = useState(false);
  const hasUnappliedChanges =
    visible.size !== applied.size ||
    [...visible].some((id) => !applied.has(id));
  const groups = Array.from(new Set(options.map((option) => option.group ?? "Cards")));
  // Closing without applying drops the draft, so reopening starts from what's shown.
  const close = () => {
    if (hasUnappliedChanges) {
      for (const option of options) {
        if (visible.has(option.id) !== applied.has(option.id)) onToggle(option.id, applied.has(option.id));
      }
    }
    setOpen(false);
  };

  return (
    <>
      <div className="section-picker-bar">
        <span className="num">{applied.size} of {options.length} cards shown</span>
        <Button kind="gray" size="sm" icon="layers" onClick={() => setOpen(true)}>Customize</Button>
      </div>
      {open && (
        <Sheet
          title="Customize view"
          subtitle={description}
          onClose={close}
          width={620}
          footer={
            <div className="section-picker-footer">
              <Button kind="plain" size="sm" onClick={onShowAll} disabled={visible.size === options.length}>{showAllLabel}</Button>
              <span className="num" aria-live="polite">{visible.size} selected</span>
              <Button kind="filled" onClick={() => { onSubmit(); setOpen(false); }} disabled={!hasUnappliedChanges}>Apply</Button>
            </div>
          }
        >
          <div className="section-picker-groups">
            {groups.map((group) => {
              const items = options.filter((option) => (option.group ?? "Cards") === group);
              const on = items.filter((option) => visible.has(option.id)).length;
              return (
                <fieldset className="section-picker-group" key={group}>
                  <legend><span>{group}</span><small className="num">{on} of {items.length}</small></legend>
                  <div className="section-picker-options">
                    {items.map((option) => (
                      <label className="section-picker-option" key={option.id}>
                        <span>{option.label}</span>
                        <input
                          type="checkbox"
                          role="switch"
                          className="v-switch"
                          checked={visible.has(option.id)}
                          onChange={(event) => onToggle(option.id, event.target.checked)}
                        />
                      </label>
                    ))}
                  </div>
                </fieldset>
              );
            })}
          </div>
        </Sheet>
      )}
    </>
  );
}

export const pretty = (value: string) =>
  value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());

export const money = (value: number) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(value);

export function activityTitle(stage: string, toolName: string | null) {
  const value = stage.toLowerCase();
  if (value.includes("intake") || value.includes("received"))
    return "Lead received";
  if (value.includes("context") || value.includes("analysis"))
    return "Context analyzed";
  if (value.includes("icp")) return "ICP evaluated";
  if (value.includes("qualif")) return "Qualification completed";
  if (value.includes("rout")) return "Route selected";
  if (value.includes("notif") || toolName?.toLowerCase().includes("slack"))
    return "Slack notified";
  if (value.includes("crm") || value.includes("sync"))
    return "CRM synchronized";
  return pretty(toolName || stage);
}

export function ActivityFeed({
  data,
  compact = false,
}: {
  data: AgentActivity;
  compact?: boolean;
}) {
  const rows = data.activity.slice(0, compact ? 7 : 25);
  return (
    <div className={`activity-list ${compact ? "activity-compact" : ""}`}>
      {rows.map((item, index) => (
        <details
          key={item.id}
          style={{ "--activity-order": index } as CSSProperties}
        >
          <summary>
            <span className="activity-sequence">
              {String(index + 1).padStart(2, "0")}
            </span>
            <i className={`activity-status status-${item.status}`} />
            <span>
              <b>{activityTitle(item.stage, item.tool_name)}</b>
              <small>
                {item.tool_name || item.agent} ·{" "}
                {new Date(item.created_at).toLocaleString()}
              </small>
            </span>
            <span className="activity-runtime">
              {item.duration_ms != null
                ? `${item.duration_ms}ms`
                : item.provider || "internal"}
            </span>
            <em>{pretty(item.status)}</em>
          </summary>
          {!compact && (
            <>
              <pre>{JSON.stringify(item.structured_result, null, 2)}</pre>
              {item.error && <p>{item.error}</p>}
            </>
          )}
        </details>
      ))}
      {rows.length === 0 && (
        <Empty
          title="No agent events"
          text="Audited activity will appear here as leads move through the system."
        />
      )}
    </div>
  );
}

export function Panel({
  title,
  eyebrow,
  action,
  children,
  collapsible = false,
}: {
  title: string;
  eyebrow: string;
  action?: string;
  children: ReactNode;
  collapsible?: boolean;
}) {
  const navigationSection = title.toLowerCase().replace(/[^a-z0-9]+/g, "-");

  if (collapsible) {
    return (
      <details
        className="panel data-panel settings-collapsible"
        data-navigation-section={navigationSection}
      >
        <summary className="settings-collapsible-summary">
          <div className="settings-collapsible-heading">
            <span className="eyebrow">{eyebrow}</span>
            <h2>{title}</h2>
          </div>
          {action && <span className="panel-action">{action}</span>}
          <span className="settings-collapsible-icon" aria-hidden="true" />
        </summary>
        <div className="settings-collapsible-content">{children}</div>
      </details>
    );
  }

  return (
    <section
      className="panel data-panel"
      data-navigation-section={navigationSection}
      tabIndex={-1}
    >
      <div className="section-heading">
        <div>
          <span className="eyebrow">{eyebrow}</span>
          <h2>{title}</h2>
        </div>
        {action && <span className="panel-action">{action}</span>}
      </div>
      {children}
    </section>
  );
}

export function LeadRow({
  lead,
  onClick,
}: {
  lead: DashboardAnalytics["recent_leads"][number];
  onClick: () => void;
}) {
  return (
    <button className="compact-lead" onClick={onClick}>
      <span className={`source-monogram source-${lead.source}`}>
        {lead.source.slice(0, 2).toUpperCase()}
      </span>
      <span>
        <b>{lead.company}</b>
        <small>
          {lead.name} · {lead.service}
        </small>
      </span>
      <span className="compact-lead-qualification">
        <b>{lead.score}</b>
        <QualificationBadge
          value={lead.band}
          manualReview={lead.manual_review}
        />
      </span>
      <i>→</i>
    </button>
  );
}

export const QualificationBadge = memo(function QualificationBadge({
  value,
  manualReview = false,
}: {
  value: string;
  manualReview?: boolean;
}) {
  const label = manualReview ? "Manual Review" : qualificationLabel(value);
  const tone = manualReview
    ? "manual-review"
    : value
        .toLowerCase()
        .replaceAll("_", "-")
        .replace(/[^a-z0-9-]/g, "-");
  return (
    <span className={`qualification-badge qualification-${tone}`}>{label}</span>
  );
});

export function qualificationLabel(value: string) {
  const normalized = value.toLowerCase().replaceAll("-", "_");
  const labels: Record<string, string> = {
    hot: "Hot",
    warm: "Warm",
    needs_qualifying: "Needs Qualifying",
    nurture: "Nurture",
    disqualify: "Disqualified",
    disqualified: "Disqualified",
    manual_review: "Manual Review",
    pending: "Pending",
  };
  return labels[normalized] ?? pretty(value);
}

export function Empty({ title, text }: { title: string; text: string }) {
  return (
    <div className="empty-state">
      <i />
      <b>{title}</b>
      <span>{text}</span>
    </div>
  );
}

export function Unavailable() {
  return (
    <div className="page-view">
      <Empty
        title="Analytics unavailable"
        text="Retry when the API is reachable."
      />
    </div>
  );
}

export function metricValue(data: DashboardAnalytics, key: string) {
  return data.metrics.find((metric) => metric.key === key)?.value ?? 0;
}
