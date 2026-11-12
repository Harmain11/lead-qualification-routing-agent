import type { ReactNode } from "react";
import type { LeadMeetingIndicator } from "../../components/LeadMeetingPanel";
import type { Lead } from "../../types";
import { Icon, type IconName } from "../ui/Icon";
import { Button } from "../ui/primitives";

type Tone = "danger" | "attention" | "tint" | "success" | "neutral";

export type LeadHighlight = {
  id: string;
  tone: Tone;
  icon: IconName;
  title: string;
  detail?: ReactNode;
  action?: ReactNode;
  priority: number;
};

type Props = {
  lead: Lead;
  review: boolean;
  reviewReasons: string[];
  meeting: LeadMeetingIndicator | null;
  slaDue: { minutes: number; overdue: boolean } | null;
  canEdit: boolean;
  canViewEmail: boolean;
  canManageManualReview: boolean;
  completingReview: boolean;
  onCompleteReview: () => void;
  onOpenSection: (section: "email" | "pipeline" | "ai") => void;
  onOpenAppointmentDraft?: () => void;
  onEditDetails: () => void;
  onRefreshMeeting: () => void;
  formatDate: (value: string) => string;
  formatDuration: (minutes: number) => string;
};

const MISSING_FIELDS: Array<[keyof Lead, string]> = [
  ["email", "email"],
  ["phone", "phone"],
  ["company_name", "company"],
  ["job_title", "job title"],
];

function list(items: string[]): string {
  if (items.length <= 1) return items[0] ?? "";
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

/** Everything that needs attention on this lead, most urgent first, in one place. */
export function buildLeadHighlights(props: Props): LeadHighlight[] {
  const { lead, review, reviewReasons, meeting, slaDue } = props;
  const items: LeadHighlight[] = [];
  const score = lead.qualification?.total_score;

  if (slaDue?.overdue) {
    items.push({
      id: "sla", tone: "danger", icon: "timer", priority: 0,
      title: `First response ${props.formatDuration(slaDue.minutes)} overdue`,
      detail: "Reply now. The response deadline set by routing has passed.",
      action: props.canViewEmail && props.canEdit && lead.email
        ? <Button kind="filled" size="sm" onClick={() => props.onOpenSection("email")}>Reply</Button>
        : undefined,
    });
  }

  if (review) {
    const reason = reviewReasons[0] ?? "It matched one of your review rules.";
    const highScore = typeof score === "number" && score >= 70;
    items.push({
      id: "review", tone: "attention", icon: "hand", priority: 1,
      title: "On hold for review",
      detail: (
        <>
          {highScore ? `Scored ${score}, but held: ` : ""}{reason}
          {reviewReasons.length > 1 && (
            <details className="v-highlight-more">
              <summary>{reviewReasons.length - 1} more condition{reviewReasons.length > 2 ? "s" : ""}</summary>
              <ul>{reviewReasons.slice(1).map((item) => <li key={item}>{item}</li>)}</ul>
            </details>
          )}
        </>
      ),
      action: props.canManageManualReview
        ? <Button kind="filled" size="sm" disabled={props.completingReview} onClick={props.onCompleteReview}>{props.completingReview ? "Completing…" : "Mark review done"}</Button>
        : <Button kind="plain" size="sm" onClick={() => props.onOpenSection("ai")}>Why</Button>,
    });
  }

  if (meeting) {
    const openEmail = props.canViewEmail
      ? <Button kind="tinted" size="sm" onClick={() => { props.onOpenAppointmentDraft?.(); props.onOpenSection("email"); }}>Open draft</Button>
      : undefined;
    const followUp = <Button kind="plain" size="sm" onClick={() => props.onOpenSection("pipeline")}>Open meetings</Button>;
    const meetingItem: Record<LeadMeetingIndicator["kind"], Omit<LeadHighlight, "id" | "icon">> = {
      appointment_needed: {
        tone: "attention", priority: 2, title: "Appointment required",
        detail: "They proposed a time. Review the booking draft, or check alternative available slots in Follow-up.",
        action: followUp,
      },
      draft_ready: {
        tone: "tint", priority: 2, title: "Appointment draft ready",
        detail: props.canViewEmail ? "Review the Calendly booking draft, then send it so they can confirm the proposed time or choose an available alternative." : "A teammate with email access needs to review and send the appointment draft.",
        action: openEmail,
      },
      sending: { tone: "neutral", priority: 5, title: "Booking email is sending", detail: "Its delivery status appears here when Gmail confirms it." },
      awaiting_booking: {
        tone: "neutral", priority: 5, title: "Booking link sent",
        detail: "Waiting for them to pick a time. Nothing is booked until the calendar confirms.",
        action: <Button kind="plain" size="sm" onClick={props.onRefreshMeeting}>Refresh</Button>,
      },
      booked: {
        tone: "success", priority: 6,
        title: meeting.bookedAt ? `Meeting booked for ${props.formatDate(meeting.bookedAt)}` : "Meeting booked",
        detail: "Confirmed by the calendar provider.",
      },
      needs_attention: { tone: "danger", priority: 2, title: "Meeting booking needs attention", detail: "The calendar couldn't complete the booking.", action: followUp },
      cancelled: {
        tone: "attention", priority: 3,
        title: meeting.cancelledMeetingAt ? `Meeting on ${props.formatDate(meeting.cancelledMeetingAt)} was cancelled` : "Meeting was cancelled",
        detail: meeting.cancellationReason ? `Reason: ${meeting.cancellationReason}. Offer new times when you're ready.` : "Offer new times when you're ready.",
        action: followUp,
      },
    };
    items.push({ id: "meeting", icon: "calendar", ...meetingItem[meeting.kind] });
  }

  const missing = MISSING_FIELDS.filter(([field]) => !lead[field]).map(([, label]) => label);
  if (missing.length) {
    items.push({
      id: "missing", tone: "neutral", icon: "edit", priority: 7,
      title: `Missing ${list(missing)}`,
      detail: "Complete the record so routing, calling and email work for this lead.",
      action: props.canEdit ? <Button kind="plain" size="sm" onClick={props.onEditDetails}>Add details</Button> : undefined,
    });
  }

  if (lead.analysis?.status === "provider_error") {
    items.push({
      id: "ai", tone: "neutral", icon: "info", priority: 8,
      title: "Scored by rules, not AI",
      detail: "The AI analysis didn't complete, so a rules-based assessment was used.",
    });
  }

  const next = lead.routing?.action_plan?.immediate_action?.trim();
  if (next && !review && !items.some((item) => item.tone === "danger")) {
    items.push({ id: "next", tone: "tint", icon: "arrowRight", priority: 9, title: "Next step", detail: next });
  }

  return items.sort((a, b) => a.priority - b.priority);
}

export function LeadHighlights(props: Props) {
  const items = buildLeadHighlights(props);
  if (!items.length) return null;
  return (
    <section className="v-highlights" aria-label="What needs attention on this lead">
      {items.map((item) => (
        <div key={item.id} className="v-highlight" data-tone={item.tone} role={item.tone === "danger" ? "alert" : undefined}>
          <span className="v-highlight-icon" aria-hidden="true"><Icon name={item.icon} size={16} /></span>
          <div className="v-highlight-copy">
            <b>{item.title}</b>
            {item.detail && <div className="v-highlight-detail">{item.detail}</div>}
          </div>
          {item.action && <div className="v-highlight-action">{item.action}</div>}
        </div>
      ))}
    </section>
  );
}
