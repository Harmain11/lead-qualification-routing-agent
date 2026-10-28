import { useEffect, useMemo, useRef, useState } from "react";
import { createLeadMeetingBookingLinkDraft, getLeadMeetingRecommendations, retryConfirmedMeetingBooking, syncLeadGmailConversation } from "../api";
import type { Lead, MeetingRecommendations, MeetingSuggestion } from "../types";

type Props = {
  lead: Lead;
  refreshToken?: number;
  canEdit: boolean;
  canViewEmails: boolean;
  onDraftCreated?: () => void;
};
type LoadState = "loading" | "ready" | "error";

const providerName = (provider: string) => provider === "google_calendar" ? "Google Calendar" : provider === "calendly" ? "Calendly" : provider;
const slotKey = (slot: MeetingSuggestion) => [slot.provider, slot.connection_id, slot.source_message_id, slot.start_at, slot.end_at].join("|");

function dateLabel(value: string, timeZone: string, options: Intl.DateTimeFormatOptions) {
  try {
    return new Intl.DateTimeFormat(undefined, { ...options, timeZone }).format(new Date(value));
  } catch {
    return new Date(value).toLocaleString();
  }
}

function meetingWindow(startAt: string, endAt: string, timeZone: string) {
  return `${dateLabel(startAt, timeZone, { weekday: "short", month: "short", day: "numeric" })} · ${dateLabel(startAt, timeZone, { hour: "numeric", minute: "2-digit" })}–${dateLabel(endAt, timeZone, { hour: "numeric", minute: "2-digit" })}`;
}

const styles = `
.lmp {
 --lmp-ink:var(--text); --lmp-muted:var(--muted); --lmp-line:var(--line);
 --lmp-wash:var(--surface-high); --lmp-green:var(--orange-hot); --lmp-green-soft:var(--orange-soft);
 --lmp-gold:var(--amber); --lmp-warning-bg:color-mix(in srgb,var(--amber) 9%,var(--surface));
 --lmp-warning-line:color-mix(in srgb,var(--amber) 35%,var(--line));
 --lmp-error-bg:color-mix(in srgb,var(--red) 9%,var(--surface));
 --lmp-error-line:color-mix(in srgb,var(--red) 35%,var(--line));
 color:var(--lmp-ink); border:1px solid var(--lmp-line); border-radius:12px; background:var(--surface); overflow:hidden;
}
.lmp * { box-sizing:border-box; }
.lmp-head { display:flex; align-items:flex-start; justify-content:space-between; gap:18px; padding:22px 24px 18px; background:var(--surface); border-bottom:1px solid var(--lmp-line); }
.lmp-head-main { display:flex; gap:13px; min-width:0; }
.lmp-mark { width:38px; height:38px; display:grid; place-items:center; flex:0 0 auto; border:1px solid var(--lmp-line); border-radius:11px; background:var(--lmp-green-soft); color:var(--lmp-green); font:700 13px/1 "Cascadia Mono","SFMono-Regular",monospace; letter-spacing:.04em; }
.lmp-eyebrow { display:block; margin:1px 0 5px; color:var(--lmp-green); font:600 13px/1.4 "Cascadia Mono","SFMono-Regular",monospace; letter-spacing:.14em; text-transform:uppercase; }
.lmp h3 { margin:0; color:var(--lmp-ink); font:600 19px/1.2 "Arial Nova","Segoe UI Variable",system-ui,sans-serif; letter-spacing:-.025em; }
.lmp-intro { margin:6px 0 0; color:var(--lmp-muted); font-size:14px; line-height:1.55; }
.lmp .lmp-refresh { display:inline-flex; align-items:center; gap:7px; min-height:44px; flex:0 0 auto; padding:0 11px; border:1px solid var(--lmp-line); border-radius:8px; background:var(--surface); color:var(--lmp-muted); font-size:14px; font-weight:650; transition:transform 160ms ease,border-color 160ms ease,background 160ms ease; }
.lmp-refresh:hover:not(:disabled) { transform:translateY(-1px); border-color:var(--orange); background:var(--lmp-wash); }
.lmp-refresh:disabled { cursor:wait; opacity:.58; }
.lmp-refresh-mark { font-size:15px; line-height:0; }
.lmp-body { padding:19px 24px 23px; }
.lmp-state { display:flex; align-items:flex-start; gap:12px; padding:14px 15px; border:1px solid var(--lmp-line); border-radius:11px; background:var(--surface); }
.lmp-state-icon { width:25px; height:25px; display:grid; place-items:center; flex:0 0 auto; border-radius:8px; background:var(--lmp-wash); color:var(--lmp-green); font:700 13px "Cascadia Mono","SFMono-Regular",monospace; }
.lmp-state h4 { margin:1px 0 5px; color:var(--lmp-ink); font-size:14px; font-weight:700; }
.lmp-state p { margin:0; color:var(--lmp-muted); font-size:14px; line-height:1.65; }
.lmp-error { border-color:var(--lmp-error-line); background:var(--lmp-error-bg); }
.lmp-error .lmp-state-icon { background:var(--lmp-error-bg); color:var(--red); }
.lmp-state-action { margin-top:10px; }
.lmp-link-button { padding:0; border:0; background:transparent; color:var(--lmp-green); font-size:14px; font-weight:700; text-decoration:underline; text-underline-offset:3px; }
.lmp-recovery-actions { display:flex; flex-wrap:wrap; align-items:center; gap:12px; margin-top:8px; }
.lmp-recovery-actions .lmp-link-button { display:inline-block; }
.lmp-calendly-types { display:grid; gap:8px; margin:14px 0; padding:12px; border:1px solid var(--lmp-line); border-radius:10px; background:var(--lmp-wash); }
.lmp-calendly-types-head { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; }
.lmp-calendly-types-head b, .lmp-calendly-type b { display:block; color:var(--lmp-ink); font-size:14px; }
.lmp-calendly-types-head small, .lmp-calendly-type small { display:block; margin-top:3px; color:var(--lmp-muted); font-size:14px; line-height:1.5; }
.lmp-calendly-type-list { display:grid; gap:6px; }
.lmp-calendly-type { display:flex; align-items:center; justify-content:space-between; gap:12px; min-height:44px; padding:8px 10px; border:1px solid var(--lmp-line); border-radius:8px; background:var(--surface); text-align:left; }
.lmp-calendly-type:not(:disabled) { cursor:pointer; }
.lmp-calendly-type:not(:disabled):hover { border-color:var(--orange); background:var(--lmp-wash); }
.lmp-calendly-type.is-selected { border-color:var(--orange); background:var(--lmp-green-soft); }
.lmp-calendly-type:disabled { cursor:not-allowed; opacity:.82; }
.lmp-type-status { flex:0 0 auto; color:var(--lmp-gold); font-size:14px; font-weight:700; }
.lmp-type-status.available { color:var(--lmp-green); }
.lmp-calendly-fix { margin-top:9px; padding-top:9px; border-top:1px solid var(--lmp-warning-line); }
.lmp-calendly-fix > b, .lmp-calendly-fallback > b { color:var(--lmp-gold); font-size:14px; }
.lmp-calendly-fix ol { margin:6px 0 6px; padding-left:18px; }
.lmp-calendly-fix li, .lmp-calendly-fix p, .lmp-calendly-fallback p { margin:4px 0; color:var(--lmp-gold); font-size:14px; line-height:1.55; }
.lmp-calendly-fallback { margin-top:12px; padding:12px; border:1px solid var(--lmp-warning-line); border-radius:9px; background:var(--lmp-warning-bg); }
.lmp-calendly-howto { margin-top:9px; color:var(--lmp-gold); font-size:14px; }
.lmp-calendly-howto summary { cursor:pointer; color:var(--lmp-gold); font-weight:700; }
.lmp-calendly-howto ol { margin:6px 0 0; padding-left:18px; }
.lmp-calendly-howto li { margin:4px 0; line-height:1.5; }
.lmp-booking-warning, .lmp-calendly-notice { margin-top:8px; padding:9px 10px; border:1px solid var(--lmp-warning-line); border-radius:8px; background:var(--lmp-warning-bg); color:var(--lmp-gold); font-size:14px; line-height:1.5; }
.lmp-booking-warning p, .lmp-calendly-notice p { margin:3px 0; }
.lmp-calendly-notice a { color:var(--lmp-gold); font-weight:700; }
.lmp-skeleton { display:grid; gap:10px; }
.lmp-skeleton-line { height:11px; border-radius:8px; background:linear-gradient(90deg,var(--lmp-wash) 25%,var(--lmp-line) 42%,var(--lmp-wash) 65%); background-size:240% 100%; animation:lmp-shimmer 1.4s ease-in-out infinite; }
@keyframes lmp-shimmer { to { background-position:-120% 0; } }
.lmp-skeleton-line:nth-child(2) { width:76%; }.lmp-skeleton-line:nth-child(3) { width:52%; }
.lmp-context { margin-bottom:18px; padding:13px 15px; border-left:2px solid var(--orange); border-radius:0 9px 9px 0; background:var(--lmp-wash); }
.lmp-context-top { display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:7px; margin-bottom:6px; }
.lmp-context-label { color:var(--lmp-muted); font:600 13px "Cascadia Mono","SFMono-Regular",monospace; letter-spacing:.11em; text-transform:uppercase; }
.lmp-subject { color:var(--lmp-ink); font-size:14px; font-weight:700; }
.lmp-excerpt { margin:0; color:var(--lmp-muted); font-size:14px; line-height:1.65; }
.lmp-section-heading { display:flex; align-items:baseline; justify-content:space-between; gap:12px; margin:0 0 11px; }
.lmp-section-heading h4 { margin:0; color:var(--lmp-ink); font-size:14px; font-weight:700; }
.lmp-count { color:var(--lmp-muted); font:13px "Cascadia Mono","SFMono-Regular",monospace; }
.lmp-groups { display:grid; gap:13px; }
.lmp-group { overflow:hidden; border:1px solid var(--lmp-line); border-radius:11px; background:var(--surface); }
.lmp-group-head { display:flex; align-items:center; gap:9px; padding:10px 12px; border-bottom:1px solid var(--lmp-line); background:var(--lmp-wash); }
.lmp-provider-mark { width:24px; height:24px; display:grid; place-items:center; border:1px solid var(--lmp-line); border-radius:7px; background:var(--surface); color:var(--lmp-green); font:700 13px "Cascadia Mono","SFMono-Regular",monospace; }
.lmp-group-head div { min-width:0; }
.lmp-group-head b { display:block; overflow:hidden; color:var(--lmp-ink); font-size:14px; text-overflow:ellipsis; var(--surface)-space:nowrap; }
.lmp-group-head small { display:block; margin-top:2px; color:var(--lmp-muted); font-size:14px; }
.lmp-options { display:grid; gap:0; }
.lmp-option { position:relative; display:grid; grid-template-columns:18px minmax(0,1fr) auto; align-items:center; gap:10px; min-height:54px; padding:10px 12px; border-bottom:1px solid var(--lmp-line); cursor:pointer; transition:background 150ms ease; }
.lmp-option:last-child { border-bottom:0; }
.lmp-option:hover { background:var(--lmp-wash); }
.lmp-option.is-selected { background:var(--lmp-green-soft); }
.lmp-option input { width:14px; height:14px; margin:0; accent-color:var(--lmp-green); }
.lmp-option-time { color:var(--lmp-ink); font-size:14px; font-weight:700; }
.lmp-option-zone { display:block; margin-top:3px; color:var(--lmp-muted); font:13px "Cascadia Mono","SFMono-Regular",monospace; }
.lmp-option-duration { color:var(--lmp-muted); font-size:14px; var(--surface)-space:nowrap; }
.lmp-footnote { margin:12px 0 0; color:var(--lmp-muted); font-size:14px; line-height:1.55; }
.lmp-integrations { display:grid; gap:7px; margin-top:13px; }
.lmp-integration { display:flex; justify-content:space-between; align-items:center; gap:12px; padding:9px 11px; border:1px solid var(--lmp-line); border-radius:8px; background:var(--surface); color:var(--lmp-muted); font-size:14px; }
.lmp-integration b { color:var(--lmp-ink); font-size:14px; font-weight:650; }
.lmp-connection { display:inline-flex; align-items:center; gap:6px; min-width:0; color:var(--lmp-muted); font-size:14px; }
.lmp-connection::before { content:""; width:6px; height:6px; border-radius:50%; background:var(--subtle); flex-shrink:0; }
.lmp-connection.connected { color:var(--lmp-green); }.lmp-connection.connected::before { background:var(--lmp-green); }
.lmp-errors { display:grid; gap:7px; margin-top:13px; }
.lmp-provider-error { padding:10px 12px; border:1px solid var(--lmp-warning-line); border-radius:9px; background:var(--lmp-warning-bg); color:var(--lmp-gold); font-size:14px; line-height:1.5; }
.lmp-provider-error b { display:block; margin-bottom:3px; color:var(--lmp-gold); font-size:14px; }
.lmp-controls { display:flex; align-items:center; justify-content:space-between; gap:14px; margin-top:16px; padding-top:14px; border-top:1px solid var(--lmp-line); }
.lmp-controls small { color:var(--lmp-muted); font-size:14px; line-height:1.45; }
.lmp-primary { min-height:44px; flex:0 0 auto; padding:0 15px; border:1px solid var(--brand-orange); border-radius:8px; background:var(--brand-orange); color:#fff; font-size:14px; font-weight:700; transition:background 150ms ease,transform 150ms ease; }
.lmp-primary:hover:not(:disabled) { transform:translateY(-1px); background:color-mix(in srgb,var(--brand-orange) 85%,black); }
.lmp-primary:disabled { cursor:not-allowed; opacity:.45; }
.lmp-review { padding:15px; border:1px solid var(--lmp-warning-line); border-radius:11px; background:var(--lmp-warning-bg); }
.lmp-review-title { display:flex; align-items:center; gap:8px; margin:0 0 9px; color:var(--lmp-gold); font-size:14px; font-weight:750; }
.lmp-review-badge { padding:4px 6px; border:1px solid var(--lmp-warning-line); border-radius:5px; background:var(--lmp-warning-bg); color:var(--lmp-gold); font:700 13px "Cascadia Mono","SFMono-Regular",monospace; letter-spacing:.06em; text-transform:uppercase; }
.lmp-review p { margin:0; color:var(--lmp-gold); font-size:14px; line-height:1.6; }
.lmp-review-slot { margin:11px 0; padding:10px 11px; border:1px solid var(--lmp-warning-line); border-radius:8px; background:var(--surface-high); color:var(--lmp-ink); font-size:14px; font-weight:700; line-height:1.5; }
.lmp-review-actions { display:flex; flex-wrap:wrap; justify-content:flex-end; gap:8px; margin-top:12px; }
.lmp-secondary { min-height:44px; padding:0 12px; border:1px solid var(--lmp-line); border-radius:8px; background:var(--surface); color:var(--lmp-muted); font-size:14px; font-weight:650; }
.lmp-secondary:hover:not(:disabled) { background:var(--lmp-wash); }
.lmp-review .lmp-primary { min-height:44px; }
.lmp-outcome { display:flex; align-items:flex-start; gap:10px; margin-bottom:14px; padding:13px 14px; border:1px solid var(--orange); border-radius:10px; background:var(--lmp-green-soft); }
.lmp-outcome-icon { width:24px; height:24px; display:grid; place-items:center; flex:0 0 auto; border-radius:50%; background:var(--lmp-green-soft); color:var(--lmp-green); font-size:14px; font-weight:800; }
.lmp-outcome h4 { margin:1px 0 4px; color:var(--lmp-green); font-size:14px; }
.lmp-outcome p { margin:0; color:var(--lmp-muted); font-size:14px; line-height:1.55; }
.lmp-outcome a { display:inline-block; margin-top:7px; color:var(--lmp-green); font-size:14px; font-weight:700; text-decoration:underline; text-underline-offset:3px; }
.lmp-outcome-failure { border-color:var(--lmp-error-line); background:var(--lmp-error-bg); }
.lmp-outcome-failure .lmp-outcome-icon { background:var(--lmp-error-bg); color:var(--red); }
.lmp-outcome-failure h4 { color:var(--red); }.lmp-outcome-failure p { color:var(--lmp-muted); }
.lmp-sr-only { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0,0,0,0); var(--surface)-space:nowrap; }
@media(max-width:640px) {
 .lmp-head { padding:17px 15px 15px; }.lmp-body { padding:15px; }.lmp h3 { font-size:17px; }
 .lmp .lmp-refresh { width:44px; padding:0; justify-content:center; font-size:0; }.lmp-refresh-mark { font-size:16px; }
 .lmp-option { grid-template-columns:18px minmax(0,1fr); }.lmp-option-duration { grid-column:2; margin-top:-3px; }
 .lmp-controls { align-items:stretch; flex-direction:column; }.lmp-controls .lmp-primary { width:100%; }
  .lmp-calendly-type { align-items:flex-start; }.lmp-type-status { margin-top:2px; }
}
`;

export function LeadMeetingPanel({
  lead,
  refreshToken,
  canEdit,
  canViewEmails,
  onDraftCreated,
}: Props) {
  const [recommendations, setRecommendations] = useState<MeetingRecommendations | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [retryCount, setRetryCount] = useState(0);
  const [selectedCalendlyEventTypeUri, setSelectedCalendlyEventTypeUri] = useState<string | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [retryingBooking, setRetryingBooking] = useState(false);
  const [retryBookingMessage, setRetryBookingMessage] = useState("");
  const [retryBookingError, setRetryBookingError] = useState("");
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [draftCreated, setDraftCreated] = useState(false);
  const actionSequence = useRef(0);
  const currentLeadId = useRef(lead.id);
  const scanGmailOnNextLoad = useRef(false);
  currentLeadId.current = lead.id;

  useEffect(() => {
    const controller = new AbortController();
    const actionId = ++actionSequence.current;
    const shouldScanGmail = scanGmailOnNextLoad.current && canEdit;
    scanGmailOnNextLoad.current = false;
    setLoadState("loading");
    setLoadError(null);
    setRecommendations(null);
    setSelectedCalendlyEventTypeUri(null);
    setSelectedKey(null);
    setReviewing(false);
    setConfirming(false);
    setRetryingBooking(false);
    setRetryBookingMessage("");
    setRetryBookingError("");
    setConfirmError(null);
    setDraftCreated(false);
    (async () => {
      if (shouldScanGmail) await syncLeadGmailConversation(lead.id);
      return getLeadMeetingRecommendations(lead.id, controller.signal);
    })()
      .then((value) => {
        if (controller.signal.aborted || actionSequence.current !== actionId) return;
        setRecommendations(value);
        setLoadState("ready");
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted || actionSequence.current !== actionId) return;
        setLoadError(reason instanceof Error ? reason.message : "Meeting recommendations could not be loaded.");
        setLoadState("error");
      });
    return () => {
      controller.abort();
      actionSequence.current += 1;
    };
  }, [lead.id, refreshToken, retryCount, canEdit]);

  const calendlyEventTypes = recommendations?.calendly_event_types ?? [];
  const visibleSuggestions = useMemo(
    () => (recommendations?.suggestions ?? []).filter((slot) =>
      !selectedCalendlyEventTypeUri
      || slot.provider !== "calendly"
      || slot.event_type_uri === selectedCalendlyEventTypeUri
    ),
    [recommendations, selectedCalendlyEventTypeUri],
  );
  const selectedSlot = useMemo(
    () => visibleSuggestions.find((slot) => slotKey(slot) === selectedKey) ?? null,
    [visibleSuggestions, selectedKey],
  );
  const groupedSuggestions = useMemo(() => {
    const groups = new Map<string, MeetingSuggestion[]>();
    for (const slot of visibleSuggestions) {
      const key = [slot.provider, slot.connection_id, slot.event_type_name || ""].join("|");
      groups.set(key, [...(groups.get(key) ?? []), slot]);
    }
    return [...groups.entries()];
  }, [visibleSuggestions]);
  const connectedCalendars = (recommendations?.integrations ?? []).filter((integration) => integration.connected);
  const hasCalendar = connectedCalendars.length > 0;
  const hasConnectedCalendly = connectedCalendars.some((integration) => integration.provider === "calendly");
  const hasCalendlySlots = (recommendations?.suggestions ?? []).some((slot) => slot.provider === "calendly");
  const hasUnavailableCalendlyEventType = calendlyEventTypes.some((eventType) => eventType.status === "calendar_unavailable");
  const hasAvailableCalendlyEventType = calendlyEventTypes.some((eventType) => eventType.status === "available");
  const calendlyCalendarError = recommendations?.provider_errors.some(
    (error) => error.code === "calendly_external_calendar_unavailable",
  ) ?? false;
  const noThread = !recommendations?.source_message_id;
  const noMeetingDetected = Boolean(recommendations && !recommendations.detected && recommendations.source_message_id);
  const bookedOfferForCurrentRequest = Boolean(
    recommendations
    && recommendations.offer_status === "booked"
    && recommendations.offer_source_message_id === recommendations.source_message_id,
  );

  const prepareBookingLinkDraft = async () => {
    if (!canEdit || !canViewEmails || confirming) return;
    const targetLeadId = lead.id;
    const actionId = ++actionSequence.current;
    setConfirming(true);
    setConfirmError(null);
    setDraftCreated(false);
    try {
      await createLeadMeetingBookingLinkDraft(targetLeadId);
      if (currentLeadId.current !== targetLeadId || actionSequence.current !== actionId) return;
      setDraftCreated(true);
      setReviewing(false);
      onDraftCreated?.();
    } catch (reason: unknown) {
      if (currentLeadId.current !== targetLeadId || actionSequence.current !== actionId) return;
      setConfirmError(reason instanceof Error ? reason.message : "The email draft could not be prepared. Refresh the recommendations and try again.");
    } finally {
      if (currentLeadId.current === targetLeadId && actionSequence.current === actionId) setConfirming(false);
    }
  };

  const retrySavedBooking = async () => {
    const offerSourceMessageId = recommendations?.offer_source_message_id;
    if (!canEdit || !offerSourceMessageId || retryingBooking) return;
    const targetLeadId = lead.id;
    const actionId = ++actionSequence.current;
    setRetryingBooking(true);
    setRetryBookingMessage("");
    setRetryBookingError("");
    try {
      const retryResult = await retryConfirmedMeetingBooking(targetLeadId, offerSourceMessageId);
      if (currentLeadId.current !== targetLeadId || actionSequence.current !== actionId) return;
      setRetryBookingMessage(retryResult.message);
      const updated = await getLeadMeetingRecommendations(targetLeadId);
      if (currentLeadId.current !== targetLeadId || actionSequence.current !== actionId) return;
      setRecommendations(updated);
      setLoadState("ready");
    } catch (reason: unknown) {
      if (currentLeadId.current !== targetLeadId || actionSequence.current !== actionId) return;
      setRetryBookingError(reason instanceof Error ? reason.message : "The saved booking could not be retried.");
    } finally {
      if (currentLeadId.current === targetLeadId && actionSequence.current === actionId) {
        setRetryingBooking(false);
      }
    }
  };

  const scheduleAnother = () => {
    setDraftCreated(false);
    setConfirmError(null);
    setReviewing(false);
    setRetryCount((count) => count + 1);
  };

  const renderLoadError = () => (
    <div className="lmp-state lmp-error" role="alert">
      <span className="lmp-state-icon">!</span>
      <div><h4>Recommendations unavailable</h4><p>{loadError || "The meeting options could not be loaded."}</p><div className="lmp-state-action"><button className="lmp-link-button" type="button" onClick={() => setRetryCount((count) => count + 1)}>Try again</button></div></div>
    </div>
  );

  const retryAvailability = () => {
    scanGmailOnNextLoad.current = false;
    setRetryCount((count) => count + 1);
  };

  const renderSkeleton = () => (
    <div className="lmp-skeleton" aria-label="Loading meeting recommendations" role="status">
      <span className="lmp-sr-only">Loading meeting recommendations</span>
      <div className="lmp-skeleton-line" /><div className="lmp-skeleton-line" /><div className="lmp-skeleton-line" />
    </div>
  );

  return <section className="lmp" aria-labelledby={`meeting-title-${lead.id}`}>
    <style>{styles}</style>
    <header className="lmp-head">
      <div className="lmp-head-main">
        <span className="lmp-mark" aria-hidden="true">MTG</span>
        <div>
          <span className="lmp-eyebrow">Gmail · calendar scheduling</span>
          <h3 id={`meeting-title-${lead.id}`}>Schedule from the lead’s email</h3>
          <p className="lmp-intro">When a lead asks to meet in Gmail, Corral prepares a personalized reply in that thread with a Calendly or Google Calendar booking link. No slot selection is needed; review, edit, and send the draft from the lead’s Emails tab.</p>
        </div>
      </div>
      <button className="lmp-refresh" type="button" onClick={() => { scanGmailOnNextLoad.current = canEdit; setRetryCount((count) => count + 1); }} disabled={loadState === "loading" || confirming} aria-label={canEdit ? "Read latest Gmail conversation and refresh meeting recommendations" : "Refresh meeting recommendations"}>
        <span className="lmp-refresh-mark" aria-hidden="true">↻</span> {canEdit ? "Read Gmail & refresh" : "Refresh"}
      </button>
    </header>

    <div className="lmp-body">
      {loadState === "loading" && renderSkeleton()}
      {loadState === "error" && renderLoadError()}
      {loadState === "ready" && recommendations && <>
        {draftCreated && <div className="lmp-outcome" role="status">
          <span className="lmp-outcome-icon" aria-hidden="true">✓</span>
          <div><h4>Appointment draft prepared</h4><p>{hasConnectedCalendly ? "Calendly" : "Google Calendar"} booking link added to a personalized reply in the lead’s Emails tab. Review or edit it before sending. Corral will mark the meeting and notify the assigned teammate when the calendar confirms a booking.</p>
            <div><button className="lmp-link-button" type="button" onClick={scheduleAnother}>Refresh meeting status</button></div>
          </div>
        </div>}

        {!draftCreated && noThread && <div className="lmp-state">
          <span className="lmp-state-icon" aria-hidden="true">G</span>
          <div><h4>No Gmail conversation found</h4><p>{recommendations.message || "Sync the lead’s Gmail conversation to review meeting intent and available times."} Refresh these recommendations after the parent finishes a Gmail sync.</p></div>
        </div>}

        {!draftCreated && noMeetingDetected && <div className="lmp-state">
          <span className="lmp-state-icon" aria-hidden="true">—</span>
          <div><h4>No meeting request detected</h4><p>{recommendations.message || "The latest conversation does not contain a meeting request or proposed time."}</p></div>
        </div>}

        {!draftCreated && !noThread && !noMeetingDetected && recommendations.detected && <>
          {(recommendations.source_subject || recommendations.source_excerpt) && <div className="lmp-context">
            <div className="lmp-context-top"><span className="lmp-context-label">Conversation evidence</span>{recommendations.source_subject && <span className="lmp-subject">{recommendations.source_subject}</span>}</div>
            {recommendations.source_excerpt && <p className="lmp-excerpt">{recommendations.source_excerpt}</p>}
            {recommendations.message && <p className="lmp-excerpt">{recommendations.message}</p>}
            {bookedOfferForCurrentRequest && <div className="lmp-booking-warning" role="status">
              <b>Meeting already booked</b>
              <p>{recommendations.booked_meeting_at ? `Meeting set for ${new Date(recommendations.booked_meeting_at).toLocaleString()}.` : "This request already has a booked meeting."} Corral will offer another meeting only after the lead sends a new request.</p>
            </div>}
            {!bookedOfferForCurrentRequest && hasCalendar && <div className="lmp-controls">
              <small>
                {hasConnectedCalendly
                  ? "Calendly is preferred when connected, even if Google Calendar is also connected."
                  : "The workspace Google Calendar appointment link will be used."}
                {" "}You can review and change the draft before sending.
              </small>
              <button
                className="lmp-primary"
                type="button"
                disabled={!canEdit || !canViewEmails || confirming}
                onClick={() => recommendations.offer_can_be_replaced
                  ? setReviewing(true)
                  : void prepareBookingLinkDraft()}
              >
                {confirming
                  ? "Preparing draft…"
                  : recommendations.offer_can_be_replaced
                    ? "Review replacement draft"
                    : "Create appointment draft"}
              </button>
            </div>}
            {recommendations.offer_status === "needs_attention" && (recommendations.offer_booking_error || recommendations.offer_has_confirmed_selection) && <div className="lmp-booking-warning" role="alert">
              <b>Booking attempt detail</b>
              {recommendations.offer_booking_error && <p>{recommendations.offer_booking_error}</p>}
              {recommendations.offer_has_confirmed_selection && <>
                <p>Retry the saved choice directly. Corral checks the connected calendar for an existing event before attempting another booking.</p>
                <div className="lmp-recovery-actions">
                  <button className="lmp-primary" type="button" onClick={() => void retrySavedBooking()} disabled={!canEdit || retryingBooking || !recommendations.offer_source_message_id}>
                    {retryingBooking ? "Retrying booking…" : "Retry confirmed booking"}
                  </button>
                </div>
              </>}
              {retryBookingMessage && <p role="status">{retryBookingMessage}</p>}
              {retryBookingError && <p role="alert">{retryBookingError}</p>}
            </div>}
            {recommendations.offer_status === "booked" && recommendations.offer_calendar_provider === "calendly" && <div className="lmp-calendly-notice" role="status">
              <b>Did the invitee miss the confirmation?</b>
              <p>Calendly controls invitee email and calendar invitations in the event type’s notification settings. Check the invitee email and confirm an email confirmation or calendar invitation is enabled.</p>
              <a href="https://calendly.com/help/calendly-scheduling-notifications" target="_blank" rel="noreferrer">Review Calendly notification settings</a>
            </div>}
          </div>}

          {!hasCalendar && <div className="lmp-state">
            <span className="lmp-state-icon" aria-hidden="true">!</span>
            <div><h4>No connected calendar account</h4><p>Connect a Google Calendar or Calendly account in Integrations settings to see bookable times. No event or message has been created.</p></div>
          </div>}

        {!draftCreated && calendlyEventTypes.length > 0 && <div className="lmp-calendly-types" aria-label="Calendly event type availability">
            <div className="lmp-calendly-types-head">
              <div><b>Calendly event types</b><small>Choose a type to filter its available times. Calendly’s connected calendars are shared across event types.</small></div>
              {selectedCalendlyEventTypeUri && <button className="lmp-link-button" type="button" onClick={() => { setSelectedCalendlyEventTypeUri(null); setSelectedKey(null); setReviewing(false); }}>Show all</button>}
            </div>
            <div className="lmp-calendly-type-list">
              {calendlyEventTypes.map((eventType) => {
                const available = eventType.status === "available";
                const statusLabel = available ? "Times available" : eventType.status === "no_times" ? "No times in this window" : "Calendar unavailable";
                return <button
                  className={`lmp-calendly-type${selectedCalendlyEventTypeUri === eventType.event_type_uri ? " is-selected" : ""}`}
                  key={eventType.event_type_uri}
                  type="button"
                  aria-pressed={selectedCalendlyEventTypeUri === eventType.event_type_uri}
                  disabled={!available}
                  onClick={() => { setSelectedCalendlyEventTypeUri(eventType.event_type_uri); setSelectedKey(null); setReviewing(false); setConfirmError(null); }}
                >
                  <span><b>{eventType.event_type_name}</b><small>{statusLabel}</small></span>
                  <span className={`lmp-type-status${available ? " available" : ""}`}>{available ? "Available" : eventType.status === "no_times" ? "No times" : "Reconnect"}</span>
                </button>;
              })}
            </div>
          </div>}

          {recommendations.suggestions.length > 0 && <>
            <div className="lmp-section-heading"><h4>Available times</h4><span className="lmp-count">{visibleSuggestions.length} option{visibleSuggestions.length === 1 ? "" : "s"}</span></div>
            <div className="lmp-groups">
              {groupedSuggestions.map(([groupKey, slots]) => {
                const first = slots[0];
                return <div className="lmp-group" key={groupKey}>
                  <div className="lmp-group-head"><span className="lmp-provider-mark" aria-hidden="true">{first.provider === "google_calendar" ? "G" : "C"}</span><div><b>{providerName(first.provider)} · {first.account_name}</b><small>{first.event_type_name || "Suggested meeting"}</small></div></div>
                  <div className="lmp-options" role="radiogroup" aria-label={`${providerName(first.provider)} times for ${first.account_name}`}>
                    {slots.map((slot) => {
                      const key = slotKey(slot);
                      const duration = Math.max(0, Math.round((new Date(slot.end_at).getTime() - new Date(slot.start_at).getTime()) / 60000));
                      return <label key={key} className={`lmp-option${selectedKey === key ? " is-selected" : ""}`}>
                         <input type="radio" name={`meeting-slot-${lead.id}`} value={key} checked={selectedKey === key} disabled={!canEdit || confirming || draftCreated || bookedOfferForCurrentRequest} onChange={() => { setSelectedKey(key); setReviewing(false); setConfirmError(null); }} />
                        <span><span className="lmp-option-time">{meetingWindow(slot.start_at, slot.end_at, slot.time_zone)}</span><span className="lmp-option-zone">{slot.time_zone}</span></span>
                        <span className="lmp-option-duration">{duration} min</span>
                      </label>;
                    })}
                  </div>
                </div>;
              })}
            </div>
            <p className="lmp-footnote">Times are tied to the source email and selected workspace account. The connected account owner will host the meeting.</p>
          </>}

          {recommendations.suggestions.length === 0 && hasCalendar && <div className="lmp-state">
            <span className="lmp-state-icon" aria-hidden="true">…</span>
            <div><h4>No available times yet</h4><p>{recommendations.message || "The connected account did not return an available slot for this conversation."} Refresh to check again.</p></div>
          </div>}
        </>}

        {recommendations.integrations.length > 0 && <div className="lmp-integrations" aria-label="Calendar connection status">
          {recommendations.integrations.map((integration) => <div className="lmp-integration" key={`${integration.provider}-${integration.account_name || "account"}`}>
            <b>{providerName(integration.provider)}{integration.account_name ? ` · ${integration.account_name}` : ""}</b>
            <span className={`lmp-connection${integration.connected ? " connected" : ""}`}>{integration.connected ? "Connected" : integration.message || "Not connected"}</span>
          </div>)}
        </div>}

        {recommendations.provider_errors.length > 0 && <div className="lmp-errors" aria-label="Provider issues">
          {recommendations.provider_errors.map((error, index) => <div className="lmp-provider-error" key={`${error.provider}-${index}`}><b>{providerName(error.provider)}{error.retryable ? " · can retry" : ""}</b>{error.message}
            {error.code === "calendly_external_calendar_unavailable" && <div className="lmp-calendly-fix">
              <b>Fix this in Calendly</b>
              <ol>
                <li>Open Calendly’s calendar settings and reconnect the disconnected Google, Outlook, or Exchange calendar.</li>
                <li>If needed, connect another calendar and choose which calendars to check for conflicts and where to add new bookings.</li>
                <li>Return here and check availability again.</li>
              </ol>
              <p>Calendly applies connected calendars across the account; a new event type alone will not repair a disconnected calendar. {hasAvailableCalendlyEventType ? "Choose an event type marked “Available” above to use its time slots." : "No checked event type currently has working availability; reconnect or connect a calendar before checking again."}</p>
              <details className="lmp-calendly-howto">
                <summary>How to create a Calendly event type</summary>
                <ol>
                  <li>In Calendly, open Scheduling and choose <strong>+ Create</strong>.</li>
                  <li>Choose an event type, such as One-on-one, then set its name, duration, location, and availability.</li>
                  <li>Save the event type. Reconnect the calendar first if Calendly says it is unavailable.</li>
                </ol>
              </details>
              <div className="lmp-recovery-actions">
                <a className="lmp-link-button" href="https://calendly.com/help/how-to-reconnect-a-disconnected-calendar" target="_blank" rel="noreferrer">Reconnect a calendar</a>
                <a className="lmp-link-button" href="https://calendly.com/help/connect-your-calendar-to-calendly" target="_blank" rel="noreferrer">Connect or choose a calendar</a>
                <a className="lmp-link-button" href="https://calendly.com/help/how-to-set-up-an-event-type" target="_blank" rel="noreferrer">Create an event type</a>
                <button className="lmp-link-button" type="button" onClick={retryAvailability} disabled={confirming}>Check Calendly again</button>
              </div>
            </div>}
          </div>)}
        </div>}

        {hasConnectedCalendly && ((!hasCalendlySlots && !calendlyCalendarError) || (hasCalendlySlots && hasUnavailableCalendlyEventType)) && <div className="lmp-calendly-fallback">
          <b>{hasUnavailableCalendlyEventType ? "Some Calendly event types cannot read the connected calendar" : "Need Calendly options?"}</b>
          <p>{hasUnavailableCalendlyEventType
            ? "Choose an event type marked Available above to use its times. To restore the others, reconnect or connect the calendar in Calendly. Calendly shares connected calendars across event types."
            : "Check that Calendly has an active event type with future availability and a connected calendar. If you need one, create an event type in Calendly, then check again here."}</p>
          {!hasCalendlySlots && <details className="lmp-calendly-howto">
            <summary>How to create a Calendly event type</summary>
            <ol>
              <li>In Calendly, open Scheduling and choose <strong>+ Create</strong>.</li>
              <li>Choose an event type, such as One-on-one, then set its name, duration, location, and availability.</li>
              <li>Save it, return here, and check Calendly again.</li>
            </ol>
          </details>}
          <div className="lmp-recovery-actions">
            <a className="lmp-link-button" href="https://calendly.com/help/connect-your-calendar-to-calendly" target="_blank" rel="noreferrer">Connect a calendar</a>
            <a className="lmp-link-button" href="https://calendly.com/help/how-to-set-up-an-event-type" target="_blank" rel="noreferrer">Create an event type</a>
            <button className="lmp-link-button" type="button" onClick={retryAvailability} disabled={confirming}>Check Calendly again</button>
          </div>
        </div>}

        {!draftCreated && selectedSlot && !reviewing && !confirmError && !bookedOfferForCurrentRequest && <div className="lmp-controls">
            <small>
              {!canViewEmails
                ? "Email access is required to open and review the meeting draft."
                : canEdit
              ? "Selecting a time is optional. The appointment draft uses the connected booking link."
              : "You can review the booking page; an editor can prepare the email draft."}
            </small>
          <button className="lmp-primary" type="button" disabled={!canEdit || !canViewEmails || confirming} onClick={() => void prepareBookingLinkDraft()}>Create appointment draft</button>
        </div>}
        {!draftCreated && reviewing && !bookedOfferForCurrentRequest && <div className="lmp-review" role="group" aria-label="Create appointment booking-link draft">
          <div className="lmp-review-title"><span className="lmp-review-badge">Final review</span> {recommendations.offer_can_be_replaced ? "Replace the unbooked appointment draft?" : "Prepare the booking-link draft?"}</div>
          <p>This saves an editable reply in the lead’s Emails tab. It includes the preferred provider’s booking link, and you can review, change, and send it there. The lead books a time on that booking page.</p>
          {recommendations.offer_can_be_replaced && <div className="lmp-booking-warning" role="status">
            <b>Replaces the unbooked offer</b>
            <p>The previous email remains in history. The new appointment draft will use the current provider booking link.</p>
          </div>}
          {selectedSlot && <div className="lmp-review-slot">{providerName(selectedSlot.provider)} · {selectedSlot.account_name}<br />{meetingWindow(selectedSlot.start_at, selectedSlot.end_at, selectedSlot.time_zone)} · {selectedSlot.time_zone}</div>}
          {confirmError && <div className="lmp-provider-error" role="alert" style={{ marginTop: 10 }}>{confirmError}</div>}
          <div className="lmp-review-actions">
            <button className="lmp-secondary" type="button" disabled={confirming} onClick={() => { setReviewing(false); setConfirmError(null); }}>Back to availability</button>
            <button className="lmp-primary" type="button" disabled={!canEdit || !canViewEmails || confirming} onClick={() => void prepareBookingLinkDraft()}>{confirming ? "Preparing draft…" : "Create appointment draft"}</button>
          </div>
        </div>}
        {!draftCreated && confirmError && !reviewing && <div className="lmp-outcome lmp-outcome-failure" role="alert">
          <span className="lmp-outcome-icon" aria-hidden="true">!</span>
          <div><h4>Appointment draft not prepared</h4><p>{confirmError}</p><div><button className="lmp-link-button" type="button" onClick={() => void prepareBookingLinkDraft()}>Try again</button></div></div>
        </div>}
      </>}
    </div>
  </section>;
}

export default LeadMeetingPanel;
