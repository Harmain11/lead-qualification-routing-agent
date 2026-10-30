import { useEffect, useState } from "react";
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
import { Icon } from "../ui/Icon";
import { Button, formatShortDate } from "../ui/primitives";

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
 * Everything about moving this deal forward: where it is in the journey, what
 * the next stage needs, its value and close date, meetings, and the outcome.
 */
export function LeadPipeline({
  lead,
  view,
  canEdit,
  canViewEmail,
  meetingRefresh,
  onMove,
  onDraftCreated,
  onMeetingStatusChange,
}: {
  lead: Lead;
  view: DealView;
  canEdit: boolean;
  canViewEmail: boolean;
  meetingRefresh: number;
  onMove: (target: DealStage) => void;
  onDraftCreated: () => void;
  onMeetingStatusChange: (status: LeadMeetingIndicator | null) => void;
}) {
  const store = useDeals();
  const editable = canEdit && store.syncStatus === "saved";
  const [members, setMembers] = useState<Member[]>([]);
  const [valueDraft, setValueDraft] = useState<string | null>(null);
  const deal = view.deal;
  const route = lead.routing;
  const closed = view.stage === "won" || view.stage === "lost";
  const currentIndex = stageIndex(view.stage);
  const next = !closed ? OPEN_JOURNEY[currentIndex + 1] : undefined;
  const plan = route?.action_plan.follow_up_strategy?.length
    ? route.action_plan.follow_up_strategy
    : route ? [route.action_plan.immediate_action, route.action_plan.next_step_recommendation].filter(Boolean) : [];

  useEffect(() => {
    if (!editable) return;
    const controller = new AbortController();
    getLeadClosingOptions(controller.signal).then((rows) => { if (!controller.signal.aborted) setMembers(rows); }).catch(() => undefined);
    return () => controller.abort();
  }, [editable]);

  return (
    <div className="lp">
      <div className="lp-guide"><FeatureGuide topic="pipeline" /></div>

      <section className="v-card v-card-pad lp-journey" aria-labelledby="lp-journey-title">
        <header className="lp-head">
          <div>
            <h2 id="lp-journey-title" className="v-card-title">Deal journey</h2>
            <p className="v-card-sub">
              {view.stage === "won" ? "Won. Revenue is recorded." : view.stage === "lost" ? "Closed without a deal." : `${stageInfo(view.stage).label}${view.daysInStage != null ? ` for ${view.daysInStage} day${view.daysInStage === 1 ? "" : "s"}` : ""}. ${stageInfo(view.stage).hint}`}
            </p>
          </div>
          {editable && next && (
            <div className="lp-head-actions">
              <Button kind="gray" size="sm" onClick={() => onMove("lost")}>Mark lost</Button>
              <Button kind="filled" size="sm" trailingIcon="arrowRight" onClick={() => onMove(next.id)}>{next.verb}</Button>
            </div>
          )}
        </header>
        <ol className="lp-steps">
          {OPEN_JOURNEY.map((stage, index) => {
            const state = view.stage === "lost" ? (index <= currentIndex ? "done" : "skipped")
              : index < currentIndex ? "done" : index === currentIndex ? "current" : "todo";
            const at = enteredAt(view, stage.id);
            return (
              <li key={stage.id} className="lp-step" data-state={state}>
                <span className="lp-step-mark" aria-hidden="true">
                  {state === "done" ? <Icon name="check" size={12} strokeWidth={2.4} /> : state === "current" ? <i /> : null}
                </span>
                <div className="lp-step-body">
                  <div className="lp-step-title">
                    <b>{stage.label}</b>
                    {state === "current" && <span className="lp-now">Now</span>}
                    {at && state !== "todo" && <time dateTime={at}>{formatShortDate(at)}</time>}
                  </div>
                  {(state === "current" || state === "todo") && <p>{stage.hint}</p>}
                </div>
                {editable && state === "todo" && !closed && (
                  <button type="button" className="lp-step-move" onClick={() => onMove(stage.id)} aria-label={`${stage.verb}`}>Move here</button>
                )}
              </li>
            );
          })}
          {view.stage === "lost" && (
            <li className="lp-step" data-state="lost">
              <span className="lp-step-mark" aria-hidden="true"><Icon name="close" size={12} strokeWidth={2.4} /></span>
              <div className="lp-step-body"><div className="lp-step-title"><b>Lost</b>{enteredAt(view, "lost") && <time>{formatShortDate(enteredAt(view, "lost")!)}</time>}</div></div>
            </li>
          )}
        </ol>
      </section>

      <section className="lp-facts" aria-label="Deal facts">
        <div className="lp-fact">
          <span>Deal value</span>
          {valueDraft !== null ? (
            <form onSubmit={(event) => { event.preventDefault(); dealActions.setValue(lead.id, valueDraft.trim() === "" ? null : Math.max(0, Number(valueDraft))); setValueDraft(null); }}>
              <input autoFocus type="number" inputMode="decimal" min="0" step="1" aria-label="Deal value in USD" value={valueDraft}
                onChange={(event) => setValueDraft(event.target.value)}
                onBlur={(event) => event.currentTarget.form?.requestSubmit()}
                onKeyDown={(event) => { if (event.key === "Escape") setValueDraft(null); }} />
            </form>
          ) : (
            <button type="button" className="lp-fact-edit" disabled={!editable || view.valueSource === "won"} onClick={() => setValueDraft(deal.valueOverride != null ? String(deal.valueOverride) : view.value != null ? String(view.value) : "")}>
              <b className="num">{view.value != null ? formatMoney(view.value) : "Add value"}</b>
              {editable && view.valueSource !== "won" && <Icon name="edit" size={13} />}
            </button>
          )}
          <small>{view.valueSource === "won" ? "Recorded at close" : view.valueSource === "quote" ? `From quote ${view.quote?.number}` : view.valueSource === "opportunity" ? "Recorded opportunity" : view.valueSource === "override" ? "Set by your team" : "No recorded value"}</small>
        </div>
        <div className="lp-fact">
          <span>Weighted</span>
          <b className="num">{view.weighted != null ? formatMoney(view.weighted) : "—"}</b>
          <small>{view.weighted == null ? "Needs 5 closed deals to forecast" : "Value × recorded win rate"}</small>
        </div>
        <label className="lp-fact">
          <span>Expected close</span>
          <input type="date" disabled={!editable || closed} value={deal.expectedClose ?? ""} onChange={(event) => dealActions.setExpectedClose(lead.id, event.target.value || null)} />
          <small>{deal.expectedClose ? relativeDays(deal.expectedClose) : "Not set"}</small>
        </label>
        <label className="lp-fact">
          <span>Sourced by</span>
          <select className="v-popup" disabled={!editable} value={deal.generatedBy?.id ?? ""} onChange={(event) => {
            const member = members.find((item) => item.user_id === event.target.value);
            dealActions.setGeneratedBy(lead.id, member ? { id: member.user_id, name: member.display_name } : null);
          }}>
            <option value="">{lead.is_generated ? "Choose who sourced it" : "Inbound"}</option>
            {deal.generatedBy && !members.some((member) => member.user_id === deal.generatedBy?.id) && <option value={deal.generatedBy.id}>{deal.generatedBy.name}</option>}
            {members.map((member) => <option key={member.user_id} value={member.user_id}>{member.display_name}</option>)}
          </select>
          <small>{lead.is_generated ? "Outbound lead" : "Arrived inbound"}</small>
        </label>
      </section>

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
          <section className="v-card v-card-pad" aria-labelledby="lp-close-title">
            <h2 id="lp-close-title" className="v-card-title">Outcome</h2>
            {view.stage === "won" ? (
              <p className="lp-outcome" data-won><Icon name="trophy" size={18} />Won{lead.outcome?.closed_revenue != null ? ` · ${formatMoney(lead.outcome.closed_revenue)}` : ""}{lead.outcome?.credited_name ? ` · ${lead.outcome.credited_name}` : ""}</p>
            ) : view.stage === "lost" ? (
              <p className="lp-outcome"><Icon name="close" size={18} />Closed as lost</p>
            ) : (
              <>
                <p className="v-card-sub">Record the result when it’s decided. A win credits a teammate and updates revenue.</p>
                {editable && (
                  <div className="lp-close-actions">
                    <Button kind="filled" icon="trophy" onClick={() => onMove("won")}>Record win</Button>
                    <Button kind="gray" onClick={() => onMove("lost")}>Lost</Button>
                  </div>
                )}
              </>
            )}
          </section>
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
