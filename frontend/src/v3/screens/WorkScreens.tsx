import { useState, type ReactNode } from "react";
import { completeLeadManualReview } from "../../api";
import type { AnalyticsBundle } from "../../views/productShared";
import type { LeadCard, ManualReviewAnalytics } from "../../types";
import { formatDuration, formatMoney, OPEN_STAGES, stageInfo, useDeals, type DealView } from "../deals/dealStore";
import { leadAction } from "../shell/events";
import { Delta } from "../ui/charts";
import { Icon, type IconName } from "../ui/Icon";
import {
  BandBadge,
  Button,
  EmptyState,
  formatShortDate,
  Monogram,
  money,
  PageHeader,
  ScoreRing,
  Segmented,
  titleize,
} from "../ui/primitives";
import { sourceLabel } from "./leadFormat";
import { confirmAction } from "../ui/confirm";

/* ================================================================= Today */

function greeting(name: string) {
  const hour = new Date().getHours();
  const part = hour < 5 ? "Good evening" : hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  const first = name.trim().split(/\s+/)[0];
  return first ? `${part}, ${first}` : part;
}

const bandKey = (band: string) => band.trim().toLowerCase().replace(/[\s-]+/g, "_");

type Focus = {
  key: string;
  leadId: string;
  name: string;
  title: string;
  detail: string;
  tone: "danger" | "attention" | "info" | "tint";
  icon: IconName;
  rank: number;
  section?: "deal" | "ai";
  phone: boolean;
  email: boolean;
};

/** Turns the workspace into a short, ordered list of things a person should do next. */
function focusList(views: DealView[], review: ManualReviewAnalytics["items"]): Focus[] {
  const items: Focus[] = [];
  const byId = new Map(views.map((view) => [view.lead.id, view]));
  for (const item of review) {
    const view = byId.get(item.id);
    items.push({
      key: `review-${item.id}`,
      leadId: item.id,
      name: item.company || item.name,
      title: "Review before contacting",
      detail: item.review_reasons[0] ?? "Matched a review rule.",
      tone: "attention",
      icon: "hand",
      rank: item.sla_overdue ? 0 : 2,
      section: "ai",
      phone: Boolean(view?.lead.phone),
      email: Boolean(view?.lead.email),
    });
  }
  const reviewIds = new Set(review.map((item) => item.id));
  for (const view of views) {
    const lead = view.lead;
    const name = lead.company_name || lead.full_name || "Unnamed lead";
    const base = { leadId: lead.id, name, phone: Boolean(lead.phone), email: Boolean(lead.email) };
    if (view.slaDue && !reviewIds.has(lead.id)) {
      items.push({
        ...base,
        key: `sla-${lead.id}`,
        title: view.slaDue.overdue ? `First response ${formatDuration(view.slaDue.minutes)} late` : `Respond within ${formatDuration(view.slaDue.minutes)}`,
        detail: [lead.full_name, lead.product_or_service].filter(Boolean).join(" · "),
        tone: view.slaDue.overdue ? "danger" : "info",
        icon: "timer",
        rank: view.slaDue.overdue ? 1 : 3,
      });
    }
    const sent = view.deal.quotes.find((quote) => quote.status === "sent");
    if (sent?.sentAt && Date.now() - Date.parse(sent.sentAt) > 4 * 86_400_000 && view.stage !== "won" && view.stage !== "lost") {
      items.push({ ...base, key: `quote-${lead.id}`, title: `Follow up on quote ${sent.number}`, detail: `Sent ${formatShortDate(sent.sentAt)} · no decision yet`, tone: "tint", icon: "quote", rank: 4, section: "deal" });
    }
    if ((view.stage === "negotiation" || view.stage === "contract") && (view.daysInStage ?? 0) >= 7) {
      items.push({ ...base, key: `stall-${lead.id}`, title: `${stageInfo(view.stage).label} for ${view.daysInStage} days`, detail: view.value != null ? `${formatMoney(view.value)} at stake` : "Agree the next step", tone: "attention", icon: "handshake", rank: 5, section: "deal" });
    }
    if (view.deal.expectedClose && Date.parse(view.deal.expectedClose) < Date.now() - 86_400_000 && view.stage !== "won" && view.stage !== "lost") {
      items.push({ ...base, key: `close-${lead.id}`, title: "Expected close date has passed", detail: `Was ${formatShortDate(view.deal.expectedClose)} · update or close it`, tone: "info", icon: "calendar", rank: 6, section: "deal" });
    }
  }
  return items.sort((a, b) => a.rank - b.rank);
}

export function TodayScreen({
  data,
  views,
  userName,
  mode,
  onModeChange,
  dashboard,
  onInspect,
  onNavigate,
  canViewLeads,
  canCall,
  canEmail,
  rangeControl,
}: {
  data: AnalyticsBundle | null;
  views: DealView[];
  userName: string;
  mode: "today" | "dashboard";
  onModeChange: (mode: "today" | "dashboard") => void;
  dashboard: ReactNode;
  onInspect: (id: string, section?: "deal" | "ai") => void;
  onNavigate: (view: string) => void;
  canViewLeads: boolean;
  canCall: boolean;
  canEmail: boolean;
  rangeControl: ReactNode;
}) {
  const store = useDeals();
  const [showAll, setShowAll] = useState(false);
  const today = new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
  const header = (
    <PageHeader
      title={mode === "today" ? greeting(userName) : "Dashboard"}
      subtitle={mode === "today" ? today : "Every metric and panel for the selected period. Choose which cards appear."}
      actions={
        <>
          {rangeControl}
          <Segmented
            label="Home view"
            value={mode}
            onChange={onModeChange}
            options={[{ value: "today", label: "Today" }, { value: "dashboard", label: "Dashboard" }]}
          />
        </>
      }
    />
  );
  if (mode === "dashboard") {
    return (
      <div className="v-page" data-width="wide">
        {header}
        <div className="v-compat">{dashboard}</div>
      </div>
    );
  }

  const metrics = data?.dashboard?.metrics ?? [];
  const metric = (key: string) => metrics.find((item) => item.key === key);
  const review = data?.review?.items ?? [];
  const recent = data?.dashboard?.recent_leads ?? [];
  const bands = data?.dashboard?.band_distribution ?? data?.pipeline?.bands ?? [];
  const bandTotal = bands.reduce((sum, band) => sum + band.count, 0);
  const open = views.filter((view) => view.stage !== "won" && view.stage !== "lost");
  const pipeline = open.reduce((sum, view) => sum + (view.value ?? 0), 0);
  const weighted = open.some((view) => view.weighted != null)
    ? open.reduce((sum, view) => sum + (view.weighted ?? 0), 0) : null;
  const monthStart = new Date(new Date().getFullYear(), new Date().getMonth(), 1).getTime();
  const wonMonth = views
    .filter((view) => view.stage === "won" && view.lead.outcome?.closed_at && Date.parse(view.lead.outcome.closed_at) >= monthStart)
    .reduce((sum, view) => sum + (view.value ?? 0), 0);
  const goal = store.monthlyGoal;
  const focus = focusList(views, review);
  const visibleFocus = showAll ? focus : focus.slice(0, 6);
  const stageTotals = OPEN_STAGES.map((stage) => {
    const items = open.filter((view) => view.stage === stage.id);
    return { stage, count: items.length, value: items.reduce((sum, view) => sum + (view.value ?? 0), 0) };
  });
  const stageMax = Math.max(1, ...stageTotals.map((item) => item.count));
  const leads = metric("total_leads");

  return (
    <div className="v-page h-page" data-width="wide" data-tour="dashboard">
      {header}

      <section className="h-kpis" aria-label="Key numbers">
        <HomeKpi label="New leads" value={leads ? leads.value.toLocaleString() : String(views.length)} change={leads?.change_percent ?? null} onClick={canViewLeads ? () => onNavigate("leads") : undefined} />
        <HomeKpi label="Open pipeline" value={formatMoney(pipeline)} detail={`${open.length} open deals`} onClick={() => onNavigate("pipeline")} />
        <HomeKpi label="Weighted forecast" value={weighted == null ? "—" : formatMoney(weighted)} detail={weighted == null ? "Not tracked: no recorded close rate" : "Recorded cohort win rate"} onClick={() => onNavigate("revenue")} accent />
        <HomeKpi
          label="Won this month"
          value={formatMoney(wonMonth)}
          detail={goal ? `${Math.round((wonMonth / goal) * 100)}% of ${formatMoney(goal, "USD", true)} goal` : "No goal set"}
          progress={goal ? Math.min(1, wonMonth / goal) : undefined}
          onClick={() => onNavigate("revenue")}
        />
      </section>

      <div className="h-grid">
        <section className="v-card h-focus" aria-labelledby="focus-title">
          <header className="h-card-head">
            <div>
              <h2 id="focus-title" className="v-card-title">Your focus</h2>
              <p className="v-card-sub">{focus.length ? `${focus.length} thing${focus.length === 1 ? "" : "s"} worth doing now, most urgent first.` : "Nothing is waiting on you."}</p>
            </div>
            {review.length > 0 && <Button kind="plain" size="sm" onClick={() => onNavigate("review")}>Review queue</Button>}
          </header>
          {focus.length === 0 ? (
            <EmptyState icon="check" title="You’re all caught up">New leads, overdue responses, stalled deals and unanswered quotes will appear here.</EmptyState>
          ) : (
            <ul className="h-focus-list">
              {visibleFocus.map((item) => (
                <li key={item.key} data-tone={item.tone}>
                  <span className="h-focus-icon"><Icon name={item.icon} size={16} /></span>
                  <button type="button" className="h-focus-main" onClick={() => canViewLeads && onInspect(item.leadId, item.section)} disabled={!canViewLeads}>
                    <b>{item.name}</b>
                    <span className="h-focus-title">{item.title}</span>
                    <small>{item.detail}</small>
                  </button>
                  <span className="h-focus-actions">
                    {canCall && item.phone && <button type="button" className="b-act" aria-label={`Call ${item.name}`} title="Call" onClick={() => leadAction(item.leadId, "call")}><Icon name="phone" size={14} /></button>}
                    {canEmail && item.email && <button type="button" className="b-act" aria-label={`Email ${item.name}`} title="Email" onClick={() => leadAction(item.leadId, "email")}><Icon name="mail" size={14} /></button>}
                    {canViewLeads && <button type="button" className="b-act" aria-label={`Open ${item.name}`} title="Open" onClick={() => onInspect(item.leadId, item.section)}><Icon name="chevronRight" size={14} /></button>}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {focus.length > 6 && (
            <button type="button" className="h-more" onClick={() => setShowAll((value) => !value)}>{showAll ? "Show fewer" : `Show ${focus.length - 6} more`}</button>
          )}
        </section>

        <section className="v-card v-card-pad h-flow" aria-labelledby="flow-title">
          <header className="h-card-head h-card-head-flat">
            <div>
              <h2 id="flow-title" className="v-card-title">Pipeline</h2>
              <p className="v-card-sub">{open.length} open · {formatMoney(pipeline, "USD", true)}</p>
            </div>
            <Button kind="plain" size="sm" trailingIcon="chevronRight" onClick={() => onNavigate("pipeline")}>Board</Button>
          </header>
          <ul className="h-stages">
            {stageTotals.map(({ stage, count, value }) => (
              <li key={stage.id} data-stage={stage.id}>
                <span className="h-stage-label">{stage.label}</span>
                <span className="h-stage-bar" aria-hidden="true"><i style={{ transform: `scaleX(${count / stageMax})` }} /></span>
                <span className="h-stage-count num">{count}</span>
                <span className="h-stage-value num">{value ? formatMoney(value, "USD", true) : ""}</span>
              </li>
            ))}
          </ul>
        </section>

        <section className="v-card h-recent" aria-labelledby="recent-title">
          <header className="h-card-head">
            <div>
              <h2 id="recent-title" className="v-card-title">Just arrived</h2>
              <p className="v-card-sub">The newest leads, already scored and routed.</p>
            </div>
            {canViewLeads && <Button kind="plain" size="sm" onClick={() => onNavigate("leads")}>All Leads</Button>}
          </header>
          {recent.length ? (
            <ul className="v-list">
              {recent.slice(0, 5).map((lead) => <LeadListItem key={lead.id} lead={lead} onOpen={canViewLeads ? () => onInspect(lead.id) : undefined} />)}
            </ul>
          ) : (
            <EmptyState icon="leads" title="No leads yet">Connect a source or share your website form, and new leads land here.</EmptyState>
          )}
        </section>

        <section className="v-card v-card-pad h-mix" aria-labelledby="mix-title">
          <h2 id="mix-title" className="v-card-title">Qualification mix</h2>
          <p className="v-card-sub num">{bandTotal.toLocaleString()} scored lead{bandTotal === 1 ? "" : "s"} this period</p>
          {bandTotal > 0 ? (
            <>
              <div className="v-mixbar" aria-hidden="true">
                {bands.filter((band) => band.count > 0).map((band) => (
                  <i key={band.band} data-band={bandKey(band.band)} style={{ flexGrow: band.count }} />
                ))}
              </div>
              <ul className="v-legend">
                {bands.map((band) => (
                  <li key={band.band}>
                    <i data-band={bandKey(band.band)} aria-hidden="true" />
                    <span>{bandKey(band.band) === "disqualify" ? "Disqualified" : titleize(bandKey(band.band))}</span>
                    <b className="num">{band.count}</b>
                  </li>
                ))}
              </ul>
            </>
          ) : <p className="k-empty">No scored leads in this period.</p>}
        </section>
      </div>
    </div>
  );
}

function HomeKpi({ label, value, detail, change, progress, onClick, accent }: { label: string; value: string; detail?: string; change?: number | null; progress?: number; onClick?: () => void; accent?: boolean }) {
  const body = (
    <>
      <span className="h-kpi-label">{label}</span>
      <b className="num">{value}</b>
      {change != null ? <Delta value={change} /> : detail ? <small>{detail}</small> : null}
      {progress != null && <span className="h-kpi-progress" aria-hidden="true"><i style={{ transform: `scaleX(${progress})` }} /></span>}
      {change != null && detail && <small>{detail}</small>}
    </>
  );
  return onClick ? (
    <button type="button" className="h-kpi" data-accent={accent || undefined} onClick={onClick}>{body}</button>
  ) : (
    <div className="h-kpi" data-accent={accent || undefined}>{body}</div>
  );
}

function LeadListItem({ lead, onOpen, detail, attention, overdue }: { lead: LeadCard; onOpen?: () => void; detail?: string; attention?: boolean; overdue?: boolean }) {
  const body = (
    <>
      <Monogram text={lead.company || lead.name} size={34} />
      <span className="v-list-text">
        <b>{lead.company || lead.name}</b>
        <small>{detail ?? [lead.name, lead.service].filter(Boolean).join(" · ")}</small>
      </span>
      <span className="v-list-meta">
        {overdue && <span className="v-badge" data-tone="danger">Overdue</span>}
        <span className="v-list-owner">{lead.assigned_rep || "Unassigned"}</span>
        <BandBadge band={lead.band} review={attention || lead.manual_review} />
        <ScoreRing score={lead.score} band={lead.band} review={attention || lead.manual_review} size={28} label="" />
      </span>
    </>
  );
  return (
    <li>
      {onOpen ? (
        <button type="button" className="v-list-item" onClick={onOpen} aria-label={`${lead.company || lead.name}, score ${lead.score}. Open lead.`}>
          {body}
          <Icon name="chevronRight" size={14} className="v-list-chevron" />
        </button>
      ) : (
        <div className="v-list-item">{body}</div>
      )}
    </li>
  );
}

/* ================================================================ Review */

export function ReviewScreen({
  data,
  newLeadIds,
  onInspect,
  canManageReview,
  onReviewCompleted,
}: {
  data: ManualReviewAnalytics | null;
  newLeadIds: ReadonlySet<string>;
  onInspect: (id: string) => void;
  canManageReview: boolean;
  onReviewCompleted: () => void;
}) {
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function completeReview(id: string, name: string) {
    if (!(await confirmAction({ title: `Mark ${name}'s review done?`, message: "This clears the hold, applies current routing and may notify the assignee.", confirmLabel: "Mark done" }))) return;
    setBusyId(id);
    setError(null);
    try {
      await completeLeadManualReview(id);
      onReviewCompleted();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The review could not be completed.");
    } finally {
      setBusyId(null);
    }
  }
  if (!data) return <Unavailable />;
  const overdue = data.items.filter((item) => item.sla_overdue).length;
  return (
    <div className="v-page">
      <PageHeader
        title="Review"
        subtitle={
          data.total
            ? `${data.total} lead${data.total === 1 ? "" : "s"} held for a person to decide${overdue ? ` · ${overdue} past their response target` : ""}.`
            : "Leads that match one of your review rules wait here for a person."
        }
      />
      {error && <p role="alert">{error}</p>}
      {data.items.length === 0 ? (
        <div className="v-card"><EmptyState icon="check" title="Nothing to review">No leads need a person’s judgement in this period.</EmptyState></div>
      ) : (
        <ul className="v-review" data-tour="review">
          {data.items.map((lead) => (
            <li key={lead.id} className="v-card v-review-item">
              <div className="v-review-main">
                <Monogram text={lead.company || lead.name} size={44} />
                <div className="v-review-text">
                  <h2>
                    {newLeadIds.has(lead.id) && <i className="v-dot" role="img" aria-label="New lead" />}
                    {lead.company || lead.name}
                  </h2>
                  <p>{[lead.name, lead.service, sourceLabel(lead.source)].filter(Boolean).join(" · ")}</p>
                </div>
                <ScoreRing score={lead.score} band={lead.band} review size={44} />
              </div>
              <ul className="v-reasons" aria-label="Why it’s held">
                {lead.review_reasons.map((reason) => <li key={reason}>{reason}</li>)}
              </ul>
              <footer className="v-review-foot">
                <span className="v-meta">
                  {titleize(lead.routing_status)}
                  <span className="v-sep-dot">Received {formatShortDate(lead.received_at)}</span>
                  {lead.priority && <span className="v-sep-dot">{titleize(lead.priority)} priority</span>}
                </span>
                {lead.sla_overdue && <span className="v-badge" data-tone="danger">Past response target</span>}
                {canManageReview && <Button kind="plain" size="sm" disabled={busyId !== null} onClick={() => void completeReview(lead.id, lead.name || lead.company || "this lead")}>{busyId === lead.id ? "Completing…" : "Mark review done"}</Button>}
                <Button kind="tinted" size="sm" trailingIcon="chevronRight" onClick={() => onInspect(lead.id)}>Review Evidence</Button>
              </footer>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function Unavailable() {
  return (
    <div className="v-page">
      <div className="v-card"><EmptyState icon="info" title="Not available yet">This information hasn’t loaded, or your role doesn’t include it. Try refreshing, or ask a workspace admin.</EmptyState></div>
    </div>
  );
}
