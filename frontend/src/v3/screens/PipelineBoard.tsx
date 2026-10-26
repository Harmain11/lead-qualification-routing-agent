import { useMemo, useState, type DragEvent } from "react";
import type { Lead } from "../../types";
import {
  DEAL_STAGES,
  OPEN_STAGES,
  formatDuration,
  formatMoney,
  stageInfo,
  useDeals,
  type DealStage,
  type DealView,
} from "../deals/dealStore";
import { StageMoveSheet } from "../deals/StageMoveSheet";
import { leadAction } from "../shell/events";
import { Icon } from "../ui/Icon";
import {
  Banner,
  Button,
  EmptyState,
  Menu,
  MenuItem,
  MenuSeparator,
  Monogram,
  PageHeader,
  ScoreRing,
  Segmented,
} from "../ui/primitives";

type Props = {
  views: DealView[];
  loading: boolean;
  error: string | null;
  truncated: boolean;
  total: number;
  canEdit: boolean;
  canCall: boolean;
  canEmail: boolean;
  onInspect: (leadId: string) => void;
  onMoved: (lead: Lead | null) => void;
};

const money = (value: number) => formatMoney(value, "USD", true);

export function PipelineBoard({ views, loading, error, truncated, total, canEdit: editPermission, canCall, canEmail, onInspect, onMoved }: Props) {
  const { syncStatus } = useDeals();
  const canEdit = editPermission && syncStatus === "saved";
  const [mode, setMode] = useState<"board" | "list">(() => {
    try { return window.localStorage.getItem("corral-pipeline-mode") === "list" ? "list" : "board"; } catch { return "board"; }
  });
  const [owner, setOwner] = useState("all");
  const [query, setQuery] = useState("");
  const [showClosed, setShowClosed] = useState(true);
  const [dragging, setDragging] = useState<string | null>(null);
  const [over, setOver] = useState<DealStage | null>(null);
  const [move, setMove] = useState<{ view: DealView; target: DealStage } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const owners = useMemo(() => {
    const names = new Map<string, string>();
    views.forEach((view) => names.set(view.ownerId ?? "unassigned", view.owner ?? "Unassigned"));
    return [...names.entries()].sort((a, b) => a[1].localeCompare(b[1]));
  }, [views]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return views.filter((view) => {
      if (owner !== "all" && (view.ownerId ?? "unassigned") !== owner) return false;
      if (!needle) return true;
      const lead = view.lead;
      return `${lead.company_name} ${lead.full_name} ${lead.product_or_service} ${view.owner ?? ""}`.toLowerCase().includes(needle);
    });
  }, [views, owner, query]);

  const open = filtered.filter((view) => view.stage !== "won" && view.stage !== "lost");
  const won = filtered.filter((view) => view.stage === "won");
  const lost = filtered.filter((view) => view.stage === "lost");
  const openValue = open.reduce((sum, view) => sum + (view.value ?? 0), 0);
  const weighted = open.some((view) => view.weighted != null)
    ? open.reduce((sum, view) => sum + (view.weighted ?? 0), 0) : null;
  const wonValue = won.reduce((sum, view) => sum + (view.value ?? 0), 0);
  const winRate = won.length + lost.length ? won.length / (won.length + lost.length) : null;
  const avgDeal = won.filter((view) => view.value != null).length ? wonValue / won.filter((view) => view.value != null).length : null;
  const unvalued = open.filter((view) => view.value == null).length;

  const columns = (showClosed ? DEAL_STAGES : OPEN_STAGES).map((stage) => {
    const items = filtered
      .filter((view) => view.stage === stage.id)
      .sort((a, b) => (b.value ?? -1) - (a.value ?? -1) || Date.parse(b.lead.received_at) - Date.parse(a.lead.received_at));
    return { stage, items, value: items.reduce((sum, view) => sum + (view.value ?? 0), 0), weighted: items.reduce((sum, view) => sum + (view.weighted ?? 0), 0) };
  });

  const requestMove = (view: DealView, target: DealStage) => {
    if (!canEdit || view.stage === target) return;
    setMove({ view, target });
  };
  const onDrop = (event: DragEvent, target: DealStage) => {
    event.preventDefault();
    const id = event.dataTransfer.getData("text/corral-lead") || dragging;
    setOver(null);
    setDragging(null);
    const view = views.find((item) => item.lead.id === id);
    if (view) requestMove(view, target);
  };
  const changeMode = (next: "board" | "list") => {
    setMode(next);
    try { window.localStorage.setItem("corral-pipeline-mode", next); } catch { /* optional */ }
  };

  return (
    <div className="v-page b-page" data-width="full">
      <PageHeader
        title="Pipeline"
        subtitle={loading && !views.length ? "Loading deals…" : `${open.length} open deal${open.length === 1 ? "" : "s"} · ${formatMoney(openValue)} in play`}
        actions={<Segmented label="Pipeline layout" value={mode} onChange={changeMode} options={[{ value: "board", label: "Board" }, { value: "list", label: "List" }]} />}
      />

      <section className="b-metrics" aria-label="Pipeline summary">
        <Metric label="Open pipeline" value={formatMoney(openValue)} detail={unvalued ? `${unvalued} without a value` : `${open.length} deals`} />
        <Metric label="Weighted forecast" value={weighted == null ? "—" : formatMoney(weighted)} detail={weighted == null ? "Not tracked: no recorded close rate" : "Value × recorded cohort win rate"} accent />
        <Metric label="Won" value={formatMoney(wonValue)} detail={`${won.length} deal${won.length === 1 ? "" : "s"}`} />
        <Metric label="Win rate" value={winRate == null ? "—" : `${Math.round(winRate * 100)}%`} detail={`${won.length} won · ${lost.length} lost`} />
        <Metric label="Average deal" value={avgDeal == null ? "—" : formatMoney(avgDeal)} detail="Across won deals with a value" />
      </section>

      <div className="b-toolbar" role="search">
        <label className="v-searchfield b-search">
          <Icon name="search" size={15} />
          <span className="v-sr">Search deals</span>
          <input type="search" placeholder="Search company, contact, service or owner" value={query} onChange={(event) => setQuery(event.target.value)} />
        </label>
        <label className="b-filter">
          <Icon name="person" size={15} />
          <span className="v-sr">Owner</span>
          <select className="v-popup" value={owner} onChange={(event) => setOwner(event.target.value)} aria-label="Filter by owner">
            <option value="all">Everyone</option>
            {owners.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
          </select>
        </label>
        <label className="v-toggle-inline">
          <input type="checkbox" role="switch" className="v-switch" checked={showClosed} onChange={(event) => setShowClosed(event.target.checked)} />
          <span>Show closed</span>
        </label>
        {canEdit && mode === "board" && <span className="b-hint"><Icon name="grip" size={14} />Drag a deal to move it</span>}
      </div>

      {error && <Banner tone="danger" title="Deals couldn’t be loaded">{error}</Banner>}
      {notice && <Banner tone="tint" title={notice} action={<Button kind="plain" size="sm" onClick={() => setNotice(null)}>Dismiss</Button>} />}
      {truncated && <Banner title={`Showing the newest ${views.length} of ${total} leads.`}>Older leads are still in the Leads list.</Banner>}

      {mode === "board" ? (
        <div className="b-board" role="list" aria-label="Pipeline stages" aria-busy={loading}>
          {columns.map(({ stage, items, value, weighted: columnWeighted }) => (
            <section
              key={stage.id}
              role="listitem"
              className="b-col"
              data-stage={stage.id}
              data-over={over === stage.id || undefined}
              data-empty={items.length === 0 || undefined}
              data-dragging={dragging ? true : undefined}
              aria-label={`${stage.label}: ${items.length} deals, ${formatMoney(value)}`}
              onDragOver={(event) => {
                if (!dragging || !canEdit) return;
                event.preventDefault();
                event.dataTransfer.dropEffect = "move";
                if (over !== stage.id) setOver(stage.id);
              }}
              onDragLeave={(event) => {
                if (!event.currentTarget.contains(event.relatedTarget as Node)) setOver((current) => (current === stage.id ? null : current));
              }}
              onDrop={(event) => onDrop(event, stage.id)}
            >
              <header className="b-col-head">
                <span className="b-col-dot" aria-hidden="true" />
                <h2>{stage.label}</h2>
                <span className="b-col-count num">{items.length}</span>
                <span className="b-col-value num" title={weighted == null ? "Weighted forecast not tracked" : `Weighted ${formatMoney(columnWeighted)}`}>{value ? money(value) : ""}</span>
              </header>
              <div className="b-col-body">
                {items.map((view) => (
                  <DealCard
                    key={view.lead.id}
                    view={view}
                    canEdit={canEdit}
                    canCall={canCall}
                    canEmail={canEmail}
                    dragging={dragging === view.lead.id}
                    onDragStart={(event) => {
                      event.dataTransfer.setData("text/corral-lead", view.lead.id);
                      event.dataTransfer.effectAllowed = "move";
                      // Changing the source element during dragstart cancels the drag in Chromium.
                      const id = view.lead.id;
                      window.setTimeout(() => setDragging(id), 0);
                    }}
                    onDragEnd={() => { setDragging(null); setOver(null); }}
                    onOpen={() => onInspect(view.lead.id)}
                    onMove={(target) => requestMove(view, target)}
                  />
                ))}
                {!items.length && <p className="b-col-empty">{dragging ? "Drop here" : loading ? "Loading…" : "No deals"}</p>}
              </div>
            </section>
          ))}
        </div>
      ) : (
        <DealList views={filtered} canEdit={canEdit} onOpen={onInspect} onMove={requestMove} />
      )}

      {!loading && !views.length && !error && (
        <div className="v-card"><EmptyState icon="pipeline" title="No deals yet">Every lead becomes a deal here. New leads appear in the first column.</EmptyState></div>
      )}

      {move && (
        <StageMoveSheet
          view={move.view}
          target={move.target}
          onClose={() => setMove(null)}
          onMoved={(lead, message) => {
            setMove(null);
            setNotice(message);
            onMoved(lead);
          }}
        />
      )}
    </div>
  );
}

function Metric({ label, value, detail, accent }: { label: string; value: string; detail: string; accent?: boolean }) {
  return (
    <div className="b-metric" data-accent={accent || undefined}>
      <span>{label}</span>
      <b className="num">{value}</b>
      <small>{detail}</small>
    </div>
  );
}

function DealCard({
  view,
  canEdit,
  canCall,
  canEmail,
  dragging,
  onDragStart,
  onDragEnd,
  onOpen,
  onMove,
}: {
  view: DealView;
  canEdit: boolean;
  canCall: boolean;
  canEmail: boolean;
  dragging: boolean;
  onDragStart: (event: DragEvent) => void;
  onDragEnd: () => void;
  onOpen: () => void;
  onMove: (target: DealStage) => void;
}) {
  const lead = view.lead;
  const name = lead.company_name || lead.full_name || "Unnamed lead";
  const review = Boolean(lead.routing?.manual_review || lead.analysis?.requires_manual_review);
  const next = DEAL_STAGES[DEAL_STAGES.findIndex((stage) => stage.id === view.stage) + 1];
  return (
    <article
      className="b-card"
      draggable={canEdit}
      data-dragging={dragging || undefined}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      aria-label={`${name}${view.value != null ? `, ${formatMoney(view.value)}` : ""}, ${stageInfo(view.stage).label}`}
      onClick={(event) => {
        if ((event.target as HTMLElement).closest(".b-card-actions, button, a, [role=menu]")) return;
        onOpen();
      }}
    >
      <div className="b-card-top">
        <Monogram text={name} size={30} />
        <span className="b-card-name">
          <button type="button" className="b-card-title" onClick={onOpen} draggable={false}>{name}</button>
          <small>{lead.full_name && lead.company_name ? lead.full_name : lead.product_or_service || "—"}</small>
        </span>
        <ScoreRing score={lead.qualification?.total_score} band={lead.qualification?.classification} review={review} size={26} label="" />
      </div>
      <div className="b-card-value">
        {view.value != null ? (
          <>
            <b className="num">{formatMoney(view.value)}</b>
            <small>{view.valueSource === "quote" ? `Quote ${view.quote?.number ?? ""}` : view.valueSource === "opportunity" ? "Recorded opportunity" : view.valueSource === "won" ? "Recorded" : "Set by your team"}</small>
          </>
        ) : (
          <span className="b-card-novalue">No value yet</span>
        )}
      </div>
      <footer className="b-card-foot">
        <span className="b-owner" title={view.owner ?? "Unassigned"}>
          <span className="b-avatar" aria-hidden="true">{(view.owner ?? "?").slice(0, 1)}</span>
          <span>{view.owner ?? "Unassigned"}</span>
        </span>
        {view.slaDue ? (
          <span className="b-pill" data-tone={view.slaDue.overdue ? "danger" : "attention"} title={`First response due ${new Date(view.slaDue.at).toLocaleString()}`}>
            <Icon name="timer" size={12} />{view.slaDue.overdue ? `${formatDuration(view.slaDue.minutes)} late` : `${formatDuration(view.slaDue.minutes)} left`}
          </span>
        ) : view.daysInStage != null ? (
          <span className="b-days num" title="Days in this stage">{view.daysInStage}d</span>
        ) : null}
        {view.stageSource === "workspace" && <span className="b-device" title="Stage recorded in this workspace"><Icon name="check" size={12} /></span>}
      </footer>
      <div className="b-card-actions">
        {canCall && lead.phone && (
          <button type="button" className="b-act" aria-label={`Call ${name}`} title="Call" onClick={() => leadAction(lead.id, "call")}><Icon name="phone" size={14} /></button>
        )}
        {canEmail && lead.email && (
          <button type="button" className="b-act" aria-label={`Email ${name}`} title="Email" onClick={() => leadAction(lead.id, "email")}><Icon name="mail" size={14} /></button>
        )}
        {canEdit && next && view.stage !== "won" && view.stage !== "lost" && (
          <button type="button" className="b-act" aria-label={`Move ${name} to ${next.label}`} title={`Move to ${next.label}`} onClick={() => onMove(next.id)}><Icon name="arrowRight" size={14} /></button>
        )}
        {canEdit && (
          <Menu label={`Move ${name}`} iconOnly="more" trigger={null} triggerClassName="b-act">
            {(close) => (
              <>
                <span className="v-menu-label">Move to</span>
                {DEAL_STAGES.filter((stage) => stage.id !== view.stage).map((stage) => (
                  <MenuItem key={stage.id} icon={stage.id === "won" ? "trophy" : stage.id === "lost" ? "close" : "arrowRight"} onSelect={() => { close(); onMove(stage.id); }}>
                    {stage.label}
                  </MenuItem>
                ))}
                <MenuSeparator />
                <MenuItem icon="quote" onSelect={() => { close(); leadAction(lead.id, "quote"); }}>Build a quote</MenuItem>
              </>
            )}
          </Menu>
        )}
      </div>
    </article>
  );
}

function DealList({ views, canEdit, onOpen, onMove }: { views: DealView[]; canEdit: boolean; onOpen: (id: string) => void; onMove: (view: DealView, target: DealStage) => void }) {
  const [sort, setSort] = useState<"value" | "stage" | "age">("value");
  const rows = [...views].sort((a, b) =>
    sort === "value"
      ? (b.value ?? -1) - (a.value ?? -1)
      : sort === "stage"
        ? DEAL_STAGES.findIndex((stage) => stage.id === b.stage) - DEAL_STAGES.findIndex((stage) => stage.id === a.stage)
        : (b.daysInStage ?? 0) - (a.daysInStage ?? 0),
  );
  return (
    <div className="v-tablewrap b-list" role="region" aria-label="Deals" tabIndex={0}>
      <table className="v-table">
        <thead>
          <tr>
            <th scope="col">Deal</th>
            <th scope="col"><button type="button" className="b-sort" aria-pressed={sort === "stage"} onClick={() => setSort("stage")}>Stage</button></th>
            <th scope="col" className="num-col"><button type="button" className="b-sort" aria-pressed={sort === "value"} onClick={() => setSort("value")}>Value</button></th>
            <th scope="col" className="num-col">Weighted</th>
            <th scope="col">Owner</th>
            <th scope="col" className="num-col"><button type="button" className="b-sort" aria-pressed={sort === "age"} onClick={() => setSort("age")}>In stage</button></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((view) => {
            const name = view.lead.company_name || view.lead.full_name || "Unnamed lead";
            return (
              <tr key={view.lead.id} tabIndex={0} onClick={(event) => { if (!(event.target as HTMLElement).closest("button, select")) onOpen(view.lead.id); }} onKeyDown={(event) => { if (event.key === "Enter") onOpen(view.lead.id); }}>
                <td>
                  <span className="v-lead-cell">
                    <Monogram text={name} size={30} />
                    <span className="v-lead-name"><b>{name}</b><small>{view.lead.full_name}{view.lead.product_or_service ? ` · ${view.lead.product_or_service}` : ""}</small></span>
                  </span>
                </td>
                <td>
                  {canEdit ? (
                    <select className="v-popup b-stage-select" data-stage={view.stage} aria-label={`Stage for ${name}`} value={view.stage} onChange={(event) => onMove(view, event.target.value as DealStage)}>
                      {DEAL_STAGES.map((stage) => <option key={stage.id} value={stage.id}>{stage.label}</option>)}
                    </select>
                  ) : (
                    <span className="b-stage-pill" data-stage={view.stage}>{stageInfo(view.stage).label}</span>
                  )}
                </td>
                <td className="num-col num">{view.value != null ? formatMoney(view.value) : "—"}</td>
                <td className="num-col num">{view.weighted != null ? formatMoney(view.weighted) : "—"}</td>
                <td>{view.owner ?? "Unassigned"}</td>
                <td className="num-col num">{view.daysInStage != null ? `${view.daysInStage}d` : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
