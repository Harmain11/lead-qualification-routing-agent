import type { CSSProperties, ReactNode } from "react";
import { memo } from "react";
import type {
  AgentActivity,
  ChannelAnalytics,
  DashboardAnalytics,
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
  description,
  showAllLabel,
  onToggle,
  onShowAll,
}: {
  options: readonly VisibilityOption<T>[];
  visible: ReadonlySet<T>;
  description: string;
  showAllLabel: string;
  onToggle: (id: T, checked: boolean) => void;
  onShowAll: () => void;
}) {
  const groups = Array.from(
    new Set(
      options
        .map((option) => option.group)
        .filter((group): group is string => !!group),
    ),
  );
  const renderOptions = (
    items: readonly VisibilityOption<T>[],
    groupLabel: string,
  ) => (
    <div
      className="section-picker-options"
      role="group"
      aria-label={groupLabel}
    >
      {items.map((option) => (
        <label className="section-picker-option" key={option.id}>
          <input
            type="checkbox"
            checked={visible.has(option.id)}
            onChange={(event) => onToggle(option.id, event.target.checked)}
          />
          <span>{option.label}</span>
        </label>
      ))}
    </div>
  );

  return (
    <section
      className="section-picker panel"
      aria-labelledby="section-picker-title"
    >
      <div className="section-picker-heading">
        <div>
          <span className="eyebrow">Personal view</span>
          <h2 id="section-picker-title">Choose what to see</h2>
          <p>{description}</p>
        </div>
        <button
          className="button-quiet"
          type="button"
          onClick={onShowAll}
          disabled={visible.size === options.length}
        >
          {showAllLabel}
        </button>
      </div>
      {groups.length ? (
        <div className="section-picker-groups">
          {groups.map((group) => (
            <fieldset className="section-picker-group" key={group}>
              <legend>{group}</legend>
              {renderOptions(
                options.filter((option) => option.group === group),
                group,
              )}
            </fieldset>
          ))}
        </div>
      ) : (
        renderOptions(options, "Available sections")
      )}
      <small className="section-picker-note">
        Your selection is saved for your account on this device.
      </small>
    </section>
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
}: {
  title: string;
  eyebrow: string;
  action?: string;
  children: ReactNode;
}) {
  return (
    <section
      className="panel data-panel"
      data-navigation-section={title.toLowerCase().replace(/[^a-z0-9]+/g, "-")}
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
