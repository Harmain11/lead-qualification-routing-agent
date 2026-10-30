import type { Lead } from "../../types";
import type { LeadMeetingIndicator } from "../../components/LeadMeetingPanel";
import { formatMoney, SLA_STATUS_LABELS, stageInfo, type DealView } from "../deals/dealStore";
import { Icon, type IconName } from "../ui/Icon";
import { Button, formatShortDate } from "../ui/primitives";

type Section = "pipeline" | "deal" | "email" | "calls" | "ai";
type Fact = { label: string; value: string; tone?: "attention" | "danger" | "tint" };
type Move = { title: string; detail: string; actions: Array<{ label: string; icon: IconName; onClick: () => void; primary?: boolean }> };

function ago(date: string | null | undefined) {
  if (!date) return "—";
  const days = Math.floor((Date.now() - Date.parse(date)) / 86_400_000);
  return days <= 0 ? "Today" : days === 1 ? "Yesterday" : `${days} days ago`;
}

function dueIn(minutes: number) {
  const abs = Math.abs(minutes);
  const text = abs < 60 ? `${abs} min` : abs < 1440 ? `${Math.round(abs / 60)} h` : `${Math.round(abs / 1440)} d`;
  return minutes < 0 ? `${text} overdue` : `Due in ${text}`;
}

/**
 * The top of the Overview: what matters about this lead *now*. It changes as
 * the deal matures, from first contact through quote, negotiation and close.
 */
export function LeadStanding({
  lead,
  view,
  meeting,
  canEdit,
  canEmail,
  canCall,
  onOpenSection,
  onEmail,
}: {
  lead: Lead;
  view: DealView;
  meeting: LeadMeetingIndicator | null;
  canEdit: boolean;
  canEmail: boolean;
  canCall: boolean;
  onOpenSection: (section: Section) => void;
  onEmail: () => void;
}) {
  const route = lead.routing;
  const deal = view.deal;
  const owner = route?.assigned_rep_name || "Unassigned";
  const inStage = view.daysInStage != null ? `${view.daysInStage} day${view.daysInStage === 1 ? "" : "s"}` : "—";
  const ours = deal.negotiation.find((entry) => entry.party === "us" && entry.amount != null)?.amount ?? null;
  const theirs = deal.negotiation.find((entry) => entry.party === "client" && entry.amount != null)?.amount ?? null;
  const reach: Move["actions"] = [
    ...(canEmail && lead.email ? [{ label: "Email", icon: "mail" as IconName, onClick: onEmail, primary: true }] : []),
    ...(canCall && lead.phone ? [{ label: "Call", icon: "phone" as IconName, onClick: () => onOpenSection("calls") }] : []),
  ];
  const meetingText = meeting?.kind === "booked" && meeting.bookedAt ? `Booked ${formatShortDate(meeting.bookedAt)}`
    : meeting?.kind === "appointment_needed" ? "Requested, not booked"
    : meeting?.kind === "awaiting_booking" ? "Times sent, awaiting pick"
    : meeting?.kind === "cancelled" ? "Cancelled"
    : "None yet";

  let facts: Fact[];
  let move: Move;
  switch (view.stage) {
    case "new":
      facts = [
        { label: "Arrived", value: ago(lead.received_at) },
        { label: "Fit score", value: lead.qualification ? `${lead.qualification.total_score} / 100` : "Scoring…" },
        view.slaDue
          ? { label: "First response", value: dueIn(view.slaDue.overdue ? -Math.abs(view.slaDue.minutes) : view.slaDue.minutes), tone: view.slaDue.overdue ? "danger" : "attention" }
          : { label: "Owner", value: owner },
      ];
      move = { title: "Make the first contact", detail: route?.action_plan.immediate_action || "Reach out while the inquiry is fresh.", actions: reach };
      break;
    case "contacted":
      facts = [
        { label: "Contacted", value: inStage === "—" ? "—" : `${inStage} ago` },
        { label: "Reply", value: "Waiting", tone: (view.daysInStage ?? 0) >= 3 ? "attention" : undefined },
        { label: "Owner", value: owner },
      ];
      move = { title: "Get a reply", detail: (view.daysInStage ?? 0) >= 3 ? "It’s been a few days. Follow up with something useful, not a nudge." : "Give them a little time, then follow up with value.", actions: reach };
      break;
    case "engaged":
      facts = [
        { label: "Meeting", value: meetingText, tone: meeting?.kind === "booked" ? "tint" : meeting?.kind === "appointment_needed" ? "attention" : undefined },
        { label: "Engaged for", value: inStage },
        { label: "Owner", value: owner },
      ];
      move = meeting?.kind === "booked"
        ? { title: "Prepare for the meeting", detail: "Review the talking points and what’s still unknown before you meet.", actions: [{ label: "Talking points", icon: "listBullets", onClick: () => onOpenSection("ai"), primary: true }] }
        : { title: "Book the meeting", detail: "They’re engaged. Send times or a booking link so it doesn’t stall.", actions: [{ label: "Meetings", icon: "calendar", onClick: () => onOpenSection("pipeline"), primary: true }] };
      break;
    case "quoted": {
      // A proposal can be recorded as sent without a quote built in Corral.
      const sent = view.quote ? Boolean(view.quote.sentAt) : true;
      facts = [
        { label: "Quote", value: view.quote ? `${view.quote.number} · v${view.quote.version}` : "Sent outside Corral" },
        { label: "Value", value: view.value != null ? formatMoney(view.value) : "Not set", tone: view.value != null ? undefined : "attention" },
        { label: "Sent", value: view.quote ? (view.quote.sentAt ? ago(view.quote.sentAt) : "Not yet") : "Recorded", tone: sent ? undefined : "attention" },
      ];
      move = sent
        ? { title: "Get a decision", detail: "Ask what’s needed to move forward and who else signs off.", actions: [{ label: "Open deal", icon: "quote", onClick: () => onOpenSection("deal"), primary: true }] }
        : { title: "Send the quote", detail: "The quote is drafted but hasn’t gone out yet.", actions: [{ label: "Review quote", icon: "quote", onClick: () => onOpenSection("deal"), primary: true }] };
      break;
    }
    case "negotiation":
      facts = [
        { label: "Our last offer", value: ours != null ? formatMoney(ours) : "—" },
        { label: "Their last offer", value: theirs != null ? formatMoney(theirs) : "—" },
        { label: "Gap", value: ours != null && theirs != null ? formatMoney(Math.abs(ours - theirs)) : "—", tone: ours != null && theirs != null ? "attention" : undefined },
      ];
      move = { title: "Close the gap", detail: "Trade on scope, term or payment schedule before you trade on price.", actions: [{ label: "Log an offer", icon: "handshake", onClick: () => onOpenSection("deal"), primary: true }] };
      break;
    case "contract":
      facts = [
        { label: "Service levels", value: deal.sla ? SLA_STATUS_LABELS[deal.sla.status] : "Not drafted", tone: deal.sla?.status === "signed" ? "tint" : "attention" },
        { label: "Value", value: view.value != null ? formatMoney(view.value) : "—" },
        { label: "Expected close", value: deal.expectedClose ? formatShortDate(deal.expectedClose) : "Not set" },
      ];
      move = deal.sla?.status === "signed"
        ? { title: "Record the win", detail: "Terms are signed. Record the actual revenue and who closed it.", actions: [{ label: "Pipeline", icon: "trophy", onClick: () => onOpenSection("pipeline"), primary: true }] }
        : { title: "Get it signed", detail: "Agree the service levels and chase the signature.", actions: [{ label: "Service levels", icon: "contract", onClick: () => onOpenSection("deal"), primary: true }] };
      break;
    case "won":
      facts = [
        { label: "Revenue", value: lead.outcome?.closed_revenue != null ? formatMoney(lead.outcome.closed_revenue) : view.value != null ? formatMoney(view.value) : "—", tone: "tint" },
        { label: "Credited to", value: lead.outcome?.credited_name || "—" },
        { label: "Closed", value: lead.outcome?.closed_at ? formatShortDate(lead.outcome.closed_at) : "—" },
      ];
      move = { title: "Won", detail: "Hand over cleanly and keep the relationship warm for renewal and referrals.", actions: [] };
      break;
    default:
      facts = [
        { label: "Closed", value: lead.outcome?.closed_at ? formatShortDate(lead.outcome.closed_at) : "—" },
        { label: "Value at stake", value: view.value != null ? formatMoney(view.value) : "—" },
        { label: "Owner", value: owner },
      ];
      move = { title: "Closed as lost", detail: "The reason feeds win/loss learning in Research & Coaching.", actions: [] };
  }

  return (
    <section className="ls" data-stage={view.stage} aria-labelledby="ls-title">
      <header className="ls-head">
        <span className="ls-stage"><i aria-hidden="true" />{stageInfo(view.stage).label}</span>
        <h2 id="ls-title">Where this stands</h2>
      </header>
      <dl className="ls-facts">
        {facts.map((fact) => (
          <div key={fact.label} data-tone={fact.tone}>
            <dt>{fact.label}</dt>
            <dd className="num">{fact.value}</dd>
          </div>
        ))}
      </dl>
      <div className="ls-move">
        <span className="ls-move-icon" aria-hidden="true"><Icon name="arrowRight" size={15} /></span>
        <div>
          <b>{move.title}</b>
          <p>{move.detail}</p>
        </div>
        {canEdit && move.actions.length > 0 && (
          <div className="ls-actions">
            {move.actions.map((action) => (
              <Button key={action.label} size="sm" kind={action.primary ? "filled" : "gray"} icon={action.icon} onClick={action.onClick}>{action.label}</Button>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
