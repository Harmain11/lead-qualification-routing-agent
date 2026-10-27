import { useMemo, useState } from "react";
import type { AnalyticsBundle } from "../../views/ProductViews";
import {
  dealActions,
  formatMoney,
  OPEN_STAGES,
  quoteTotals,
  useDeals,
  type DealView,
} from "../deals/dealStore";
import { BarList, ColumnChart, GoalRing } from "../ui/charts";
import { Icon } from "../ui/Icon";
import { Badge, Button, EmptyState, Monogram, PageHeader, Segmented, formatShortDate, titleize } from "../ui/primitives";
import { sourceLabel } from "./leadFormat";

const RANGE_LABEL: Record<string, string> = { weekly: "this week", monthly: "this month", annual: "this year" };
const usd = (value: number) => formatMoney(value);
const compact = (value: number) => formatMoney(value, "USD", true);

function DataStatus({ status }: { status: string | undefined }) {
  if (!status || status === "crm" || status === "manual" || status === "mixed") return null;
  return status === "demo" ? <Badge tone="attention">Illustrative demo values</Badge> : <Badge>No recorded revenue yet</Badge>;
}

/* ================================================================ Revenue */

export function RevenueScreen({
  data,
  views,
  range,
  rangeControl,
  canEdit,
  onInspect,
}: {
  data: AnalyticsBundle | null;
  views: DealView[];
  range: string;
  rangeControl: React.ReactNode;
  canEdit: boolean;
  onInspect: (leadId: string, section?: "deal") => void;
}) {
  const store = useDeals();
  const [goalDraft, setGoalDraft] = useState<string | null>(null);
  const revenue = data?.revenue ?? null;
  const open = views.filter((view) => view.stage !== "won" && view.stage !== "lost");
  const won = views.filter((view) => view.stage === "won");
  const lost = views.filter((view) => view.stage === "lost");
  const pipeline = open.reduce((sum, view) => sum + (view.value ?? 0), 0);
  const weighted = open.some((view) => view.weighted != null)
    ? open.reduce((sum, view) => sum + (view.weighted ?? 0), 0) : null;
  const monthStart = new Date();
  monthStart.setDate(1);
  monthStart.setHours(0, 0, 0, 0);
  const wonThisMonth = won
    .filter((view) => view.lead.outcome?.closed_at && Date.parse(view.lead.outcome.closed_at) >= monthStart.getTime())
    .reduce((sum, view) => sum + (view.value ?? 0), 0);
  const goal = store.monthlyGoal;
  const closingThisMonth = open
    .filter((view) => view.deal.expectedClose && new Date(view.deal.expectedClose).getMonth() === new Date().getMonth() && new Date(view.deal.expectedClose).getFullYear() === new Date().getFullYear())
    .sort((a, b) => (b.value ?? 0) - (a.value ?? 0));
  const quotes = views
    .flatMap((view) => view.deal.quotes.filter((quote) => quote.status === "sent" || quote.status === "accepted").map((quote) => ({ view, quote, total: quoteTotals(quote).contractValue })))
    .filter((item) => item.view.stage !== "won" && item.view.stage !== "lost")
    .sort((a, b) => b.total - a.total);
  const quoted = quotes.reduce((sum, item) => sum + item.total, 0);
  const avgDeal = won.filter((view) => view.value).length ? won.reduce((sum, view) => sum + (view.value ?? 0), 0) / won.filter((view) => view.value).length : null;
  const overTime = (revenue?.over_time?.length ? revenue.over_time.map((point) => ({ label: periodLabel(point.period), value: point.value })) : data?.series?.points.map((point) => ({ label: periodLabel(point.period), value: point.revenue }))) ?? [];
  const byStage = OPEN_STAGES.map((stage) => {
    const items = open.filter((view) => view.stage === stage.id);
    return { key: stage.id, label: stage.label, value: items.reduce((sum, view) => sum + (view.weighted ?? 0), 0), count: items.length };
  });

  return (
    <div className="v-page r-page" data-width="wide">
      <PageHeader title="Revenue" subtitle={`What’s won, what’s likely, and where it comes from — ${RANGE_LABEL[range] ?? "this period"}.`} actions={rangeControl} />

      <section className="r-hero">
        <div className="v-card r-goal">
          <GoalRing value={wonThisMonth} goal={goal} label={goal ? `${Math.round((wonThisMonth / goal) * 100)}% of the monthly goal` : "No monthly goal set"}>
            <b className="num">{goal ? `${Math.min(999, Math.round((wonThisMonth / goal) * 100))}%` : "—"}</b>
            <small>{goal ? "of goal" : "No goal"}</small>
          </GoalRing>
          <div className="r-goal-text">
            <span>Won this month</span>
            <b className="num">{usd(wonThisMonth)}</b>
            {goalDraft !== null ? (
              <form className="r-goal-form" onSubmit={(event) => { event.preventDefault(); dealActions.setMonthlyGoal(goalDraft.trim() ? Math.max(0, Number(goalDraft)) : null); setGoalDraft(null); }}>
                <input autoFocus type="number" min="0" step="1000" aria-label="Monthly revenue goal in USD" value={goalDraft} onChange={(event) => setGoalDraft(event.target.value)} />
                <Button size="sm" kind="filled" type="submit">Set</Button>
              </form>
            ) : (
              <small>
                {goal ? `Goal ${usd(goal)} · ${usd(Math.max(0, goal - wonThisMonth))} to go` : "Set a goal to track the month."}
                {canEdit && <button type="button" className="r-link" onClick={() => setGoalDraft(goal ? String(goal) : "")}>{goal ? "Change" : "Set goal"}</button>}
              </small>
            )}
          </div>
        </div>
        <div className="r-kpis">
          <Kpi icon="revenue" label={`Recorded ${RANGE_LABEL[range] ?? ""}`} value={revenue ? usd(revenue.total_revenue) : "—"} foot={<DataStatus status={revenue?.data_status} />} />
          <Kpi icon="pipeline" label="Open pipeline" value={usd(pipeline)} foot={`${open.length} open deals`} />
           <Kpi icon="target" label="Weighted forecast" value={weighted == null ? "—" : usd(weighted)} foot={weighted == null ? "Not tracked: no recorded close rate" : "By recorded cohort win rate"} accent />
          <Kpi icon="quote" label="Quotes out" value={usd(quoted)} foot={`${quotes.length} sent or accepted`} />
          <Kpi icon="trophy" label="Average won deal" value={avgDeal ? usd(avgDeal) : "—"} foot={`${won.length} won`} />
          <Kpi icon="star" label="Win rate" value={won.length + lost.length ? `${Math.round((won.length / (won.length + lost.length)) * 100)}%` : "—"} foot={`${won.length} won · ${lost.length} lost`} />
        </div>
      </section>

      <section className="v-card v-card-pad r-trend">
        <header className="r-head"><h2 className="v-card-title">Revenue over time</h2><DataStatus status={revenue?.data_status} /></header>
        <ColumnChart label="Recorded revenue by period" points={overTime} format={usd} height={180} />
      </section>

      <div className="r-grid">
        <section className="v-card v-card-pad">
          <h2 className="v-card-title">Weighted forecast by stage</h2>
          <p className="v-card-sub">{weighted == null ? "Not tracked until closing outcomes are recorded." : "Saved open deal values × the recorded win rate of loaded closing outcomes."}</p>
          <BarList items={byStage.map((item) => ({ key: item.key, label: <>{item.label} <small className="r-count">{item.count}</small></>, value: item.value }))} format={compact} />
        </section>
        <section className="v-card v-card-pad">
          <h2 className="v-card-title">Revenue by source</h2>
          <p className="v-card-sub">Recorded wins by where the lead came from.</p>
          <BarList items={(revenue?.by_channel ?? []).filter((item) => item.value > 0).map((item) => ({ key: item.label, label: sourceLabel(item.label), value: item.value }))} format={compact} empty="No recorded wins by source yet." />
        </section>
        <section className="v-card v-card-pad">
          <h2 className="v-card-title">Revenue by service</h2>
          <p className="v-card-sub">What customers are buying.</p>
          <BarList items={(revenue?.by_service ?? []).filter((item) => item.value > 0).map((item) => ({ key: item.label, label: item.label, value: item.value }))} format={compact} empty="No recorded wins by service yet." />
        </section>
        <section className="v-card v-card-pad">
          <h2 className="v-card-title">Revenue by owner</h2>
          <p className="v-card-sub">Credited wins per team member.</p>
          <BarList items={(revenue?.by_rep ?? []).filter((item) => item.value > 0).map((item) => ({ key: item.label, label: item.label, value: item.value }))} format={compact} empty="No credited wins yet." />
        </section>
      </div>

      <div className="r-grid r-grid-2">
        <section className="v-card v-card-pad">
          <h2 className="v-card-title">Quotes awaiting a decision</h2>
          {quotes.length ? (
            <ul className="r-list">
              {quotes.slice(0, 8).map(({ view, quote, total }) => (
                <li key={quote.id}>
                  <button type="button" onClick={() => onInspect(view.lead.id, "deal")}>
                    <Monogram text={view.lead.company_name || view.lead.full_name} size={30} />
                    <span><b>{view.lead.company_name || view.lead.full_name}</b><small>{quote.number} · {quote.status === "accepted" ? "Accepted" : `Sent ${quote.sentAt ? formatShortDate(quote.sentAt) : ""}`}</small></span>
                    <b className="num">{usd(total)}</b>
                  </button>
                </li>
              ))}
            </ul>
          ) : <EmptyState icon="quote" title="No quotes out">Quotes you send from a lead’s Deal tab appear here until they’re decided.</EmptyState>}
        </section>
        <section className="v-card v-card-pad">
          <h2 className="v-card-title">Expected to close this month</h2>
          {closingThisMonth.length ? (
            <ul className="r-list">
              {closingThisMonth.slice(0, 8).map((view) => (
                <li key={view.lead.id}>
                  <button type="button" onClick={() => onInspect(view.lead.id, "deal")}>
                    <Monogram text={view.lead.company_name || view.lead.full_name} size={30} />
                    <span><b>{view.lead.company_name || view.lead.full_name}</b><small>{titleize(view.stage)} · {formatShortDate(view.deal.expectedClose!)}</small></span>
                    <b className="num">{view.value != null ? usd(view.value) : "—"}</b>
                  </button>
                </li>
              ))}
            </ul>
          ) : <EmptyState icon="calendar" title="Nothing dated this month">Set an expected close date on a deal to plan the month.</EmptyState>}
        </section>
      </div>
    </div>
  );
}

function periodLabel(period: string) {
  const date = /^\d{4}-\d{2}(-\d{2})?$/.test(period) ? new Date(period.length === 7 ? `${period}-01T00:00:00` : `${period}T00:00:00`) : null;
  if (!date || Number.isNaN(date.getTime())) return period;
  return period.length === 7 ? date.toLocaleDateString(undefined, { month: "short", year: "2-digit" }) : date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function Kpi({ icon, label, value, foot, accent }: { icon: string; label: string; value: string; foot?: React.ReactNode; accent?: boolean }) {
  return (
    <div className="r-kpi" data-accent={accent || undefined}>
      <span className="r-kpi-label"><Icon name={icon} size={15} />{label}</span>
      <b className="num">{value}</b>
      {foot && <small>{foot}</small>}
    </div>
  );
}

/* ============================================================ Performance */

type RepRow = {
  key: string;
  name: string;
  role: string | null;
  assigned: number;
  qualified: number;
  won: number;
  lost: number;
  active: number;
  winRate: number;
  revenue: number;
  responseMinutes: number | null;
  pipeline: number;
  weighted: number | null;
  quotes: number;
  generated: number;
  summary: string | null;
};

const METRICS: Array<{ key: keyof RepRow; label: string; format: (value: number) => string; better: "high" | "low" }> = [
  { key: "revenue", label: "Revenue won", format: usd, better: "high" },
  { key: "won", label: "Deals won", format: (value) => String(value), better: "high" },
  { key: "winRate", label: "Win rate", format: (value) => `${Math.round(value)}%`, better: "high" },
  { key: "pipeline", label: "Open pipeline", format: usd, better: "high" },
  { key: "weighted", label: "Weighted forecast", format: usd, better: "high" },
  { key: "assigned", label: "Leads assigned", format: (value) => String(value), better: "high" },
  { key: "active", label: "Active deals", format: (value) => String(value), better: "high" },
  { key: "quotes", label: "Quotes sent", format: (value) => String(value), better: "high" },
  { key: "generated", label: "Leads generated", format: (value) => String(value), better: "high" },
  { key: "responseMinutes", label: "Avg. first response", format: (value) => (value ? `${Math.round(value)} min` : "—"), better: "low" },
];

export function PerformanceScreen({
  data,
  views,
  range,
  rangeControl,
}: {
  data: AnalyticsBundle | null;
  views: DealView[];
  range: string;
  rangeControl: React.ReactNode;
}) {
  const [sort, setSort] = useState<"revenue" | "won" | "winRate" | "responseMinutes">("revenue");
  const [compare, setCompare] = useState<string[]>([]);
  const rows = useMemo<RepRow[]>(() => {
    const reps = data?.reps;
    const byName = new Map<string, RepRow>();
    const blank = (name: string): RepRow => ({ key: name, name, role: null, assigned: 0, qualified: 0, won: 0, lost: 0, active: 0, winRate: 0, revenue: 0, responseMinutes: null, pipeline: 0, weighted: null, quotes: 0, generated: 0, summary: null });
    for (const rep of reps?.reps ?? []) {
      byName.set(rep.rep_name, { ...blank(rep.rep_name), assigned: rep.assigned_leads, qualified: rep.qualified_leads, won: rep.closed_leads, winRate: rep.conversion_rate, revenue: rep.revenue, responseMinutes: rep.average_response_minutes });
    }
    for (const member of reps?.members ?? []) {
      const row = byName.get(member.display_name) ?? blank(member.display_name);
      byName.set(member.display_name, {
        ...row,
        role: member.role,
        assigned: Math.max(row.assigned, member.assigned_leads),
        qualified: Math.max(row.qualified, member.qualified_leads),
        won: Math.max(row.won, member.closed_leads),
        lost: member.lost_leads,
        active: member.active_leads,
        winRate: row.winRate || member.conversion_rate,
        revenue: Math.max(row.revenue, member.revenue),
        responseMinutes: row.responseMinutes ?? member.average_response_minutes,
        summary: member.summary,
      });
    }
    for (const view of views) {
      if (view.owner) {
        const row = byName.get(view.owner) ?? blank(view.owner);
        if (view.stage !== "won" && view.stage !== "lost") {
          row.pipeline += view.value ?? 0;
          if (view.weighted != null) row.weighted = (row.weighted ?? 0) + view.weighted;
          if (!reps?.members?.some((member) => member.display_name === view.owner)) row.active += 1;
        }
        row.quotes += view.deal.quotes.filter((quote) => quote.status !== "draft").length;
        byName.set(view.owner, row);
      }
      const generator = view.deal.generatedBy?.name;
      if (generator) {
        const row = byName.get(generator) ?? blank(generator);
        row.generated += 1;
        byName.set(generator, row);
      }
    }
    return [...byName.values()].filter((row) => row.assigned || row.won || row.pipeline || row.generated || row.quotes || row.role);
  }, [data?.reps, views]);

  const sorted = [...rows].sort((a, b) =>
    sort === "responseMinutes" ? (a.responseMinutes ?? Infinity) - (b.responseMinutes ?? Infinity) : (b[sort] as number) - (a[sort] as number),
  );
  const leader = sorted[0];
  const compared = compare.map((key) => rows.find((row) => row.key === key)).filter(Boolean) as RepRow[];
  const toggleCompare = (key: string) =>
    setCompare((current) => (current.includes(key) ? current.filter((item) => item !== key) : current.length >= 3 ? [...current.slice(1), key] : [...current, key]));
  const teamRevenue = rows.reduce((sum, row) => sum + row.revenue, 0);

  return (
    <div className="v-page t-page" data-width="wide">
      <PageHeader title="Team performance" subtitle={`How each person is converting ${RANGE_LABEL[range] ?? "this period"}, side by side.`} actions={rangeControl} />

      {!rows.length ? (
        <div className="v-card"><EmptyState icon="team" title="No assigned work yet">Performance appears once leads are assigned to team members.</EmptyState></div>
      ) : (
        <>
          <section className="t-podium" aria-label="Leaders">
            {sorted.slice(0, 3).map((row, index) => (
              <div key={row.key} className="t-podium-card" data-rank={index + 1}>
                <span className="t-rank num">{index + 1}</span>
                <Monogram text={row.name} size={44} />
                <b>{row.name}</b>
                <span className="t-podium-value num">{METRICS.find((metric) => metric.key === sort)!.format(row[sort] as number)}</span>
                <small>{METRICS.find((metric) => metric.key === sort)!.label}{sort === "revenue" && teamRevenue ? ` · ${Math.round((row.revenue / teamRevenue) * 100)}% of team` : ""}</small>
              </div>
            ))}
          </section>

          <section className="v-card t-board" aria-labelledby="t-board-title">
            <header className="t-board-head">
              <h2 id="t-board-title" className="v-card-title">Leaderboard</h2>
              <Segmented
                label="Rank by"
                size="sm"
                value={sort}
                onChange={setSort}
                options={[
                  { value: "revenue", label: "Revenue" },
                  { value: "won", label: "Wins" },
                  { value: "winRate", label: "Win rate" },
                  { value: "responseMinutes", label: "Speed" },
                ]}
              />
            </header>
            <div className="v-tablewrap" role="region" aria-label="Leaderboard" tabIndex={0}>
              <table className="v-table t-table">
                <thead>
                  <tr>
                    <th scope="col">Member</th>
                    <th scope="col" className="num-col">Revenue</th>
                    <th scope="col" className="num-col">Won</th>
                    <th scope="col" className="num-col">Win rate</th>
                    <th scope="col" className="num-col">Pipeline</th>
                    <th scope="col" className="num-col">Assigned</th>
                    <th scope="col" className="num-col">Quotes</th>
                    <th scope="col" className="num-col">Response</th>
                    <th scope="col"><span className="v-sr">Compare</span></th>
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((row) => (
                    <tr key={row.key} data-leader={row === leader || undefined}>
                      <td>
                        <span className="v-lead-cell">
                          <Monogram text={row.name} size={30} />
                          <span className="v-lead-name"><b>{row.name}{row === leader && <Icon name="star" size={13} className="t-star" />}</b><small>{row.role ? titleize(row.role) : "Representative"}</small></span>
                        </span>
                      </td>
                      <td className="num-col num">{usd(row.revenue)}</td>
                      <td className="num-col num">{row.won}</td>
                      <td className="num-col"><span className="t-rate"><span className="t-rate-track" aria-hidden="true"><i style={{ transform: `scaleX(${Math.min(1, row.winRate / 100)})` }} /></span><span className="num">{Math.round(row.winRate)}%</span></span></td>
                      <td className="num-col num">{compact(row.pipeline)}</td>
                      <td className="num-col num">{row.assigned}</td>
                      <td className="num-col num">{row.quotes}</td>
                      <td className="num-col num">{row.responseMinutes != null ? `${Math.round(row.responseMinutes)}m` : "—"}</td>
                      <td>
                        <button type="button" className="t-compare-btn" aria-pressed={compare.includes(row.key)} onClick={() => toggleCompare(row.key)}>
                          {compare.includes(row.key) ? <><Icon name="check" size={13} />Comparing</> : "Compare"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="v-card v-card-pad t-compare" aria-labelledby="t-compare-title">
            <header className="t-board-head">
              <div>
                <h2 id="t-compare-title" className="v-card-title">Compare side by side</h2>
                <p className="v-card-sub">{compared.length < 2 ? "Pick two or three people from the leaderboard." : "The stronger figure in each row is highlighted."}</p>
              </div>
              {compared.length > 0 && <Button kind="plain" size="sm" onClick={() => setCompare([])}>Clear</Button>}
            </header>
            {compared.length >= 2 ? (
              <div className="t-grid" style={{ "--cols": compared.length } as React.CSSProperties}>
                <span />
                {compared.map((row) => (
                  <div key={row.key} className="t-grid-person"><Monogram text={row.name} size={36} /><b>{row.name}</b></div>
                ))}
                {METRICS.map((metric) => {
                  const values = compared.map((row) => (row[metric.key] as number | null) ?? null);
                  const valid = values.filter((value): value is number => value != null && (metric.better === "high" || value > 0));
                  const best = valid.length ? (metric.better === "high" ? Math.max(...valid) : Math.min(...valid)) : null;
                  const max = Math.max(...values.map((value) => value ?? 0), 1);
                  return (
                    <div key={metric.key} className="t-grid-row" role="row">
                      <span className="t-grid-label">{metric.label}</span>
                      {compared.map((row, index) => {
                        const value = values[index];
                        const isBest = value != null && best != null && value === best && valid.length > 1 && values.filter((item) => item === best).length === 1;
                        return (
                          <span key={row.key} className="t-grid-cell" data-best={isBest || undefined}>
                            <b className="num">{value == null ? "—" : metric.format(value)}</b>
                            <i aria-hidden="true"><i style={{ transform: `scaleX(${value ? Math.min(1, value / max) : 0})` }} /></i>
                          </span>
                        );
                      })}
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="t-compare-pick">
                {sorted.map((row) => (
                  <button key={row.key} type="button" className="t-chip" aria-pressed={compare.includes(row.key)} onClick={() => toggleCompare(row.key)}>
                    <Monogram text={row.name} size={22} />{row.name}
                  </button>
                ))}
              </div>
            )}
          </section>

          {rows.some((row) => row.summary && row.assigned > 0) && (
            <section className="t-notes">
              {rows.filter((row) => row.summary && row.assigned > 0).map((row) => (
                <div key={row.key} className="v-card v-card-pad"><b>{row.name}</b><p className="v-card-sub">{row.summary}</p></div>
              ))}
            </section>
          )}
          <p className="t-foot"><Icon name="info" size={13} /><span>Revenue, wins, response times and deal records are shared across this workspace.</span></p>
        </>
      )}
    </div>
  );
}
