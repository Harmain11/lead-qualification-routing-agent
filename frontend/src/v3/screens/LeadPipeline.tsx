import { useEffect, useMemo, useState, type ReactNode } from "react";
import { getLeadClosingOptions } from "../../api";
import type { Lead } from "../../types";
import { LeadMeetingPanel, type LeadMeetingIndicator } from "../../components/LeadMeetingPanel";
import { FeatureGuide } from "../../components/FeatureGuide";
import {
  dealActions,
  DEAL_STAGES,
  formatMoney,
  stageIndex,
  stageInfo,
  useDeals,
  type DealStage,
  type DealView,
} from "../deals/dealStore";
import { leadAction } from "../shell/events";
import { Icon } from "../ui/Icon";
import { Button, formatShortDate, Monogram } from "../ui/primitives";

type Member = { user_id: string; display_name: string };

const OPEN_JOURNEY = DEAL_STAGES.filter((stage) => stage.id !== "lost");

/** When the deal last entered a stage, read from its own activity log. */
function enteredAt(view: DealView, stage: DealStage): string | null {
  const label = stageInfo(stage).label;
  return view.deal.activity.find((entry) => entry.kind === "stage" && entry.text.startsWith(`Moved to ${label}`))?.at ?? null;
}

function relativeDays(date: string) {
  const days = Math.round((Date.parse(date) - Date.now()) / 86_400_000);
  if (days === 0) return "Today";
  if (days === 1) return "Tomorrow";
  if (days === -1) return "Yesterday";
  return days > 0 ? `In ${days} days` : `${-days} days ago`;
}

/**
 * Everything about moving this deal forward, top to bottom: where it is in the
 * journey, its numbers, an update you can log, the plan and meetings, and how
 * it sits on the pipeline board. The board and this tab share one deal store,
 * so a move made in either place shows up in both.
 */
export function LeadPipeline({
  lead,
  view,
  canEdit,
  canViewEmail,
  meetingRefresh,
  boardViews,
  updates,
  onOpenBoard,
  onMove,
  onDraftCreated,
  onMeetingStatusChange,
}: {
  lead: Lead;
  view: DealView;
  canEdit: boolean;
  canViewEmail: boolean;
  meetingRefresh: number;
  boardViews?: DealView[];
  updates?: ReactNode;
  onOpenBoard?: () => void;
  onMove: (target: DealStage) => void;
  onDraftCreated: () => void;
  onMeetingStatusChange: (status: LeadMeetingIndicator | null) => void;
}) {
  const store = useDeals();
  const editable = canEdit && store.syncStatus === "saved";
  const [members, setMembers] = useState<Member[]>([]);
  const deal = view.deal;
  const route = lead.routing;
  const closed = view.stage === "won" || view.stage === "lost";
  const currentIndex = stageIndex(view.stage);
  const next = !closed ? OPEN_JOURNEY[currentIndex + 1] : undefined;
  const plan = route?.action_plan.follow_up_strategy?.length
    ? route.action_plan.follow_up_strategy
    : route ? [route.action_plan.immediate_action, route.action_plan.next_step_recommendation].filter(Boolean) : [];
  const [valueDraft, setValueDraft] = useState<string | null>(null);

  useEffect(() => {
    if (!editable) return;
    const controller = new AbortController();
    getLeadClosingOptions(controller.signal).then((rows) => { if (!controller.signal.aborted) setMembers(rows); }).catch(() => undefined);
    return () => controller.abort();
  }, [editable]);

  const peers = useMemo(() => (boardViews ?? []).filter((item) => item.stage === view.stage && item.lead.id !== lead.id), [boardViews, view.stage, lead.id]);
  const openDeals = useMemo(() => (boardViews ?? []).filter((item) => item.stage !== "won" && item.stage !== "lost"), [boardViews]);
  const rank = useMemo(() => {
    if (view.value == null || closed) return null;
    const valued = openDeals.filter((item) => item.value != null).sort((a, b) => (b.value ?? 0) - (a.value ?? 0));
    const index = valued.findIndex((item) => item.lead.id === lead.id);
    return index === -1 ? null : { place: index + 1, of: valued.length };
  }, [openDeals, view.value, closed, lead.id]);
  const stageTotal = peers.reduce((sum, item) => sum + (item.value ?? 0), 0) + (view.value ?? 0);

  const commitValue = () => {
    if (valueDraft === null) return;
    const trimmed = valueDraft.trim();
    dealActions.setValue(lead.id, trimmed === "" ? null : Math.max(0, Number(trimmed)));
    setValueDraft(null);
  };

  const status = view.stage === "won"
    ? "Won. Revenue is recorded."
    : view.stage === "lost"
      ? "Closed without a deal."
      : `${stageInfo(view.stage).label}${view.daysInStage != null ? ` for ${view.daysInStage} day${view.daysInStage === 1 ? "" : "s"}` : ""}. ${stageInfo(view.stage).hint}`;

  return (
    <div className="lp">
      <div className="lp-toolbar">
        <FeatureGuide topic="pipeline" />
        {onOpenBoard && <Button kind="gray" size="sm" icon="pipeline" onClick={onOpenBoard}>Open pipeline board</Button>}
      </div>

      <section className="v-card v-card-pad lp-journey" aria-labelledby="lp-journey-title">
        <header className="lp-head">
          <div>
            <h2 id="lp-journey-title" className="v-card-title">Deal journey</h2>
            <p className="v-card-sub">{status}</p>
          </div>
          {editable && next && (
            <div className="lp-head-actions">
              <Button kind="gray" size="sm" onClick={() => onMove("lost")}>Mark lost</Button>
              <Button kind="filled" size="sm" trailingIcon="arrowRight" onClick={() => onMove(next.id)}>{next.verb}</Button>
            </div>
          )}
        </header>
        <ol className="lp-track" aria-label="Deal stages">
          {OPEN_JOURNEY.map((stage, index) => {
            const state = view.stage === "lost" ? (index <= currentIndex ? "done" : "skipped")
              : index < currentIndex ? "done" : index === currentIndex ? "current" : "todo";
            const at = enteredAt(view, stage.id);
            const movable = editable && state === "todo" && !closed;
            const body = (
              <>
                <span className="lp-node" aria-hidden="true">
                  {state === "done" ? <Icon name="check" size={11} strokeWidth={2.6} /> : state === "current" ? <i /> : null}
                </span>
                <span className="lp-stage-name">{stage.label}</span>
                <span className="lp-stage-when">{at && state !== "todo" ? formatShortDate(at) : state === "current" ? "Now" : ""}</span>
              </>
            );
            return (
              <li key={stage.id} className="lp-stage" data-state={state}>
                {movable ? (
                  <button type="button" className="lp-stage-btn" onClick={() => onMove(stage.id)} aria-label={`Move to ${stage.label}`} title={stage.hint}>{body}</button>
                ) : (
                  <span className="lp-stage-btn" aria-current={state === "current" ? "step" : undefined} title={stage.hint}>{body}</span>
                )}
              </li>
            );
          })}
          {view.stage === "lost" && (
            <li className="lp-stage" data-state="lost">
              <span className="lp-stage-btn">
                <span className="lp-node" aria-hidden="true"><Icon name="close" size={11} strokeWidth={2.6} /></span>
                <span className="lp-stage-name">Lost</span>
                <span className="lp-stage-when">{enteredAt(view, "lost") ? formatShortDate(enteredAt(view, "lost")!) : ""}</span>
              </span>
            </li>
          )}
        </ol>
        {next && <p className="lp-next"><Icon name="chevronRight" size={14} /><span><b>Next, {next.label}.</b> {next.hint}</span></p>}
      </section>

      <section className="lp-facts" aria-label="Deal facts">
        <div className="lp-field">
          <label htmlFor="lp-value">Deal value</label>
          <div className="lp-control lp-money" data-disabled={!editable || view.valueSource === "won" || undefined}>
            <span aria-hidden="true">$</span>
            <input
              id="lp-value"
              type="number"
              inputMode="decimal"
              min="0"
              step="1"
              placeholder="Add value"
              disabled={!editable || view.valueSource === "won"}
              value={valueDraft ?? (view.value != null ? String(view.value) : "")}
              onChange={(event) => setValueDraft(event.target.value)}
              onBlur={commitValue}
              onKeyDown={(event) => {
                if (event.key === "Enter") event.currentTarget.blur();
                if (event.key === "Escape") { setValueDraft(null); event.currentTarget.blur(); }
              }}
            />
          </div>
          <small>{view.valueSource === "won" ? "Recorded at close" : view.valueSource === "quote" ? `From quote ${view.quote?.number}` : view.valueSource === "opportunity" ? "Recorded opportunity" : view.valueSource === "override" ? "Set by your team" : "No recorded value"}</small>
        </div>
        <div className="lp-field">
          <span className="lp-label">Weighted forecast</span>
          <div className="lp-control" data-disabled><span className="num">{view.weighted != null ? formatMoney(view.weighted) : "—"}</span></div>
          <small>{view.weighted == null ? "Needs 5 closed deals to forecast" : "Value × recorded win rate"}</small>
        </div>
        <div className="lp-field">
          <label htmlFor="lp-close">Expected close</label>
          <div className="lp-control" data-disabled={!editable || closed || undefined}>
            <input id="lp-close" type="date" disabled={!editable || closed} value={deal.expectedClose ?? ""} onChange={(event) => dealActions.setExpectedClose(lead.id, event.target.value || null)} />
          </div>
          <small>{deal.expectedClose ? relativeDays(deal.expectedClose) : "Not set"}</small>
        </div>
        <div className="lp-field">
          <label htmlFor="lp-source">Sourced by</label>
          <div className="lp-control" data-disabled={!editable || undefined}>
            <select id="lp-source" disabled={!editable} value={deal.generatedBy?.id ?? ""} onChange={(event) => {
              const member = members.find((item) => item.user_id === event.target.value);
              dealActions.setGeneratedBy(lead.id, member ? { id: member.user_id, name: member.display_name } : null);
            }}>
              <option value="">{lead.is_generated ? "Choose who sourced it" : "Inbound"}</option>
              {deal.generatedBy && !members.some((member) => member.user_id === deal.generatedBy?.id) && <option value={deal.generatedBy.id}>{deal.generatedBy.name}</option>}
              {members.map((member) => <option key={member.user_id} value={member.user_id}>{member.display_name}</option>)}
            </select>
            <Icon name="chevronDown" size={14} />
          </div>
          <small>{lead.is_generated ? "Outbound lead" : "Arrived inbound"}</small>
        </div>
      </section>

      {updates}

      <div className="lp-grid">
        <div className="lp-main">
          {!closed && (
            <section className="v-card v-card-pad" aria-labelledby="lp-plan-title">
              <h2 id="lp-plan-title" className="v-card-title">{next ? `Plan to reach ${next.label}` : "Plan to close"}</h2>
              {plan.length ? (
                <ol className="lp-plan">{plan.map((step, index) => <li key={`${index}-${step}`}>{step}</li>)}</ol>
              ) : <p className="v-card-sub">A plan appears once this lead has been routed.</p>}
              {route?.action_plan.conversion_goal && <p className="lp-goal"><Icon name="target" size={14} />{route.action_plan.conversion_goal}</p>}
            </section>
          )}
          <LeadMeetingPanel
            lead={lead}
            canEdit={canEdit}
            canViewEmails={canViewEmail}
            refreshToken={meetingRefresh}
            onDraftCreated={onDraftCreated}
            onStatusChange={onMeetingStatusChange}
          />
        </div>
        <aside className="lp-side">
          {boardViews && boardViews.length > 0 && !closed && (
            <section className="v-card v-card-pad" aria-labelledby="lp-board-title">
              <h2 id="lp-board-title" className="v-card-title">On the board</h2>
              <dl className="lp-stats">
                <div><dt>In {stageInfo(view.stage).label}</dt><dd className="num">{peers.length + 1} deal{peers.length ? "s" : ""}</dd></div>
                <div><dt>Stage value</dt><dd className="num">{formatMoney(stageTotal)}</dd></div>
                {rank && <div><dt>By value</dt><dd className="num">#{rank.place} of {rank.of}</dd></div>}
              </dl>
              {peers.length > 0 && (
                <ul className="lp-peers">
                  {peers.slice(0, 4).map((item) => (
                    <li key={item.lead.id}>
                      <button type="button" onClick={() => leadAction(item.lead.id, "open")}>
                        <Monogram text={item.lead.company_name || item.lead.full_name} size={26} />
                        <span><b>{item.lead.company_name || item.lead.full_name}</b><small>{item.owner ?? "Unassigned"}{item.daysInStage != null ? ` · ${item.daysInStage}d` : ""}</small></span>
                        <span className="num">{item.value != null ? formatMoney(item.value) : "—"}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              {onOpenBoard && <Button kind="plain" size="sm" trailingIcon="arrowRight" onClick={onOpenBoard}>See all deals</Button>}
            </section>
          )}
          <section className="v-card v-card-pad" aria-labelledby="lp-activity-title">
            <h2 id="lp-activity-title" className="v-card-title">Deal activity</h2>
            {deal.activity.length ? (
              <ol className="lp-activity">
                {deal.activity.slice(0, 10).map((entry) => (
                  <li key={entry.id}><span>{entry.text}</span><time dateTime={entry.at}>{formatShortDate(entry.at)}</time></li>
                ))}
              </ol>
            ) : <p className="v-card-sub">Stage moves, value changes, quotes and offers appear here.</p>}
          </section>
        </aside>
      </div>
    </div>
  );
}
