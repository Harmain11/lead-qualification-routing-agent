import { useEffect, useMemo, useRef, useState } from "react";
import { createLeadMeetingPreferredOfferDraft, getLeadMeetingRecommendations, retryConfirmedMeetingBooking, syncLeadGmailConversation } from "../api";
import type { Lead, MeetingRecommendations, MeetingSuggestion } from "../types";
import { LeadMeetingManagement } from "./LeadMeetingManagement";
import { Icon } from "../v3/ui/Icon";
import { Button } from "../v3/ui/primitives";

type Props = {
  lead: Lead;
  refreshToken?: number;
  canEdit: boolean;
  canViewEmails: boolean;
  onDraftCreated?: () => void;
  onStatusChange?: (status: LeadMeetingIndicator | null) => void;
};
type LoadState = "loading" | "ready" | "error";

export type LeadMeetingIndicator = {
  kind: "appointment_needed" | "draft_ready" | "sending" | "awaiting_booking" | "booked" | "needs_attention" | "cancelled";
  bookedAt: string | null;
  cancelledMeetingAt?: string | null;
  cancellationReason?: string | null;
};

const providerName = (provider: string) => provider === "google_calendar" ? "Google Calendar" : provider === "calendly" ? "Calendly" : provider;
const slotKey = (slot: MeetingSuggestion) => [slot.provider, slot.connection_id, slot.source_message_id, slot.start_at, slot.end_at].join("|");

function meetingIndicatorFor(
  recommendations: MeetingRecommendations | null,
  draftCreated: boolean,
): LeadMeetingIndicator | null {
  if (!recommendations) return draftCreated ? { kind: "draft_ready", bookedAt: null } : null;
  switch (recommendations.offer_status) {
    case "awaiting_send": return { kind: "draft_ready", bookedAt: null };
    case "sending":
    case "booking": return { kind: "sending", bookedAt: null };
    case "awaiting_confirmation": return { kind: "awaiting_booking", bookedAt: null };
    case "booked": return { kind: "booked", bookedAt: recommendations.booked_meeting_at };
    case "needs_attention": return { kind: "needs_attention", bookedAt: null };
    default:
      if (draftCreated) return { kind: "draft_ready", bookedAt: null };
      if (recommendations.cancelled_at || recommendations.cancelled_meeting_at) {
        return {
          kind: "cancelled",
          bookedAt: null,
          cancelledMeetingAt: recommendations.cancelled_meeting_at ?? null,
          cancellationReason: recommendations.cancellation_reason ?? null,
        };
      }
      return recommendations.detected ? { kind: "appointment_needed", bookedAt: null } : null;
  }
}

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


export function LeadMeetingPanel({
  lead,
  refreshToken,
  canEdit,
  canViewEmails,
  onDraftCreated,
  onStatusChange,
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
  const autoDraftAttempt = useRef<string | null>(null);
  const emailOpenedForDraft = useRef<string | null>(null);
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

  useEffect(() => {
    if (loadState === "ready") {
      onStatusChange?.(
        recommendations?.lead_id === lead.id
          ? meetingIndicatorFor(recommendations, draftCreated)
          : null,
      );
    }
  }, [lead.id, loadState, recommendations, draftCreated, onStatusChange]);

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
  const noThread = !recommendations?.source_message_id && recommendations?.offer_status !== "booked";
  const noMeetingDetected = Boolean(recommendations && !recommendations.detected && recommendations.source_message_id);
  const bookedOfferForCurrentRequest = Boolean(
    recommendations
    && recommendations.offer_status === "booked"
    && recommendations.offer_source_message_id === recommendations.source_message_id,
  );

  const prepareMeetingOfferDraft = async () => {
    if (!canEdit || !canViewEmails || confirming) return;
    const targetLeadId = lead.id;
    const actionId = ++actionSequence.current;
    setConfirming(true);
    setConfirmError(null);
    setDraftCreated(false);
    try {
      await createLeadMeetingPreferredOfferDraft(targetLeadId);
      if (currentLeadId.current !== targetLeadId || actionSequence.current !== actionId) return;
      setDraftCreated(true);
      setReviewing(false);
      openEmailsForDraft();
    } catch (reason: unknown) {
      if (currentLeadId.current !== targetLeadId || actionSequence.current !== actionId) return;
      setConfirmError(reason instanceof Error ? reason.message : "The email draft could not be prepared. Refresh the recommendations and try again.");
    } finally {
      if (currentLeadId.current === targetLeadId && actionSequence.current === actionId) setConfirming(false);
    }
  };

  const openEmailsForDraft = () => {
    if (!onDraftCreated) return;
    const sourceMessageId = recommendations?.offer_source_message_id
      || recommendations?.source_message_id
      || "saved-draft";
    const key = `${lead.id}:${sourceMessageId}`;
    if (emailOpenedForDraft.current === key) return;
    emailOpenedForDraft.current = key;
    onDraftCreated();
  };

  useEffect(() => {
    if (
      loadState === "ready"
      && recommendations?.offer_status === "awaiting_send"
      && canViewEmails
    ) {
      openEmailsForDraft();
    }
  }, [
    canViewEmails,
    lead.id,
    loadState,
    onDraftCreated,
    recommendations?.offer_source_message_id,
    recommendations?.offer_status,
    recommendations?.source_message_id,
  ]);

  useEffect(() => {
    const sourceMessageId = recommendations?.source_message_id;
    if (
      loadState !== "ready"
      || !recommendations?.detected
      || !sourceMessageId
      || recommendations.offer_status
      || recommendations.offer_can_be_replaced
      || !hasCalendar
      || !canEdit
      || !canViewEmails
      || draftCreated
      || confirming
    ) {
      return;
    }
    const attemptKey = [
      lead.id,
      sourceMessageId,
      retryCount,
      refreshToken ?? 0,
    ].join(":");
    if (autoDraftAttempt.current === attemptKey) return;
    autoDraftAttempt.current = attemptKey;
    void prepareMeetingOfferDraft();
  }, [
    canEdit,
    canViewEmails,
    confirming,
    draftCreated,
    hasCalendar,
    lead.id,
    loadState,
    recommendations,
    refreshToken,
    retryCount,
  ]);

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
      <span className="lmp-state-icon" aria-hidden="true"><Icon name="alert" size={16} /></span>
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
    <header className="lmp-head">
      <span className="lmp-mark" aria-hidden="true"><Icon name="calendar" size={18} /></span>
      <div className="lmp-head-main">
        <h3 id={`meeting-title-${lead.id}`}>Meetings</h3>
        <p className="lmp-intro">When {lead.full_name?.split(" ")[0] || "the lead"} asks to meet by email, Corral finds open times on your calendar and drafts the reply for you to send. A booking only counts once your calendar confirms it.</p>
      </div>
      <Button
        kind="gray"
        size="sm"
        icon="refresh"
        onClick={() => { scanGmailOnNextLoad.current = canEdit; setRetryCount((count) => count + 1); }}
        disabled={loadState === "loading" || confirming}
        aria-label={canEdit ? "Read the latest email and refresh meeting times" : "Refresh meeting times"}
      >
        {canEdit ? "Check email" : "Refresh"}
      </Button>
    </header>

    <div className="lmp-body">
      <LeadMeetingManagement
        leadId={lead.id}
        canEdit={canEdit}
        refreshToken={refreshToken}
        onChanged={() => setRetryCount((value) => value + 1)}
        onStatusChange={onStatusChange}
      />
      {loadState === "loading" && renderSkeleton()}
      {loadState === "error" && renderLoadError()}
      {loadState === "ready" && recommendations && <>
        {draftCreated && <div className="lmp-outcome" role="status">
          <span className="lmp-outcome-icon" aria-hidden="true"><Icon name="check" size={16} /></span>
          <div><h4>Appointment draft prepared</h4><p>Open the reply in Emails to review and send it. Corral records a booking only after the connected calendar confirms the chosen time.</p>
            <div><button className="lmp-link-button" type="button" onClick={scheduleAnother}>Refresh meeting status</button></div>
          </div>
        </div>}

        {!draftCreated && noThread && <div className="lmp-state">
          <span className="lmp-state-icon" aria-hidden="true"><Icon name="mail" size={16} /></span>
          <div><h4>No meeting request yet</h4><p>Nothing in their emails asks for a meeting yet. Choose Check email after they reply.</p></div>
        </div>}

        {!draftCreated && noMeetingDetected && <div className="lmp-state">
          <span className="lmp-state-icon" aria-hidden="true"><Icon name="calendar" size={16} /></span>
          <div><h4>No meeting request detected</h4><p>{recommendations.message || "The latest conversation does not contain a meeting request or proposed time."}</p></div>
        </div>}

        {!draftCreated && !noThread && !noMeetingDetected && recommendations.detected && recommendations.source_message_id && <>
          {(recommendations.source_subject || recommendations.source_excerpt) && <div className="lmp-context">
            <div className="lmp-context-top"><span className="lmp-context-label">Conversation evidence</span>{recommendations.source_subject && <span className="lmp-subject">{recommendations.source_subject}</span>}</div>
            {recommendations.source_excerpt && <p className="lmp-excerpt">{recommendations.source_excerpt}</p>}
            {recommendations.message && <p className="lmp-excerpt">{recommendations.message}</p>}
            {bookedOfferForCurrentRequest && <div className="lmp-booking-warning" role="status">
              <b>Meeting already booked</b>
              <p>{recommendations.booked_meeting_at ? `Meeting set for ${new Date(recommendations.booked_meeting_at).toLocaleString()}.` : "This request already has a booked meeting."} Corral will offer another meeting only after the lead sends a new request.</p>
            </div>}
            {!bookedOfferForCurrentRequest && hasCalendar && <div className="lmp-controls">
              <small role="status">
                {recommendations.offer_can_be_replaced
                  ? "A previous unsent offer exists. Review before replacing it."
                  : confirmError
                    ? "The appointment draft needs attention below."
                    : confirming
                      ? "Preparing available meeting times…"
                      : !canViewEmails
                        ? "Email access is required to create and review the appointment draft."
                        : !canEdit
                          ? "A teammate with edit access must prepare the appointment draft."
                          : "Meeting request detected. Corral is preparing an editable draft for Emails."}
              </small>
              {recommendations.offer_can_be_replaced && (
                <button
                  className="lmp-primary"
                  type="button"
                  disabled={!canEdit || !canViewEmails || confirming}
                  onClick={() => setReviewing(true)}
                >
                  {confirming ? "Preparing draft…" : "Review replacement draft"}
                </button>
              )}
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
            <span className="lmp-state-icon" aria-hidden="true"><Icon name="alert" size={16} /></span>
            <div><h4>No calendar connected</h4><p>Connect Google Calendar or Calendly in Integrations to offer open times.</p></div>
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
                  <div className="lmp-group-head"><span className="lmp-provider-mark" aria-hidden="true"><Icon name="calendar" size={14} /></span><div><b>{providerName(first.provider)} · {first.account_name}</b><small>{first.event_type_name || "Suggested meeting"}</small></div></div>
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
            <span className="lmp-state-icon" aria-hidden="true"><Icon name="clock" size={16} /></span>
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
          <b>{hasUnavailableCalendlyEventType ? "Some Calendly event types cannot read the connected calendar" : "Calendly slot suggestions are unavailable"}</b>
          <p>{hasUnavailableCalendlyEventType
            ? "Choose an event type marked Available above to use its times. To restore the others, reconnect or connect the calendar in Calendly. Calendly shares connected calendars across event types."
            : "This affects individual time-slot suggestions only. Corral can still prepare a booking-link draft if your Calendly account has a public scheduling URL; the lead chooses a time on Calendly. To show suggested times here, make sure Calendly has an active event type with future availability and a connected calendar."}</p>
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

        {!draftCreated && reviewing && !bookedOfferForCurrentRequest && <div className="lmp-review" role="group" aria-label="Create appointment draft">
          <div className="lmp-review-title"><span className="lmp-review-badge">Final review</span> {recommendations.offer_can_be_replaced ? "Replace the unbooked appointment draft?" : "Prepare the meeting offer?"}</div>
          <p>This saves an editable reply in Emails with up to five current calendar times. A clear numbered choice books after another availability check. If no times are available, the reply uses the connected booking link.</p>
          {recommendations.offer_can_be_replaced && <div className="lmp-booking-warning" role="status">
            <b>Replaces the unbooked offer</b>
            <p>The previous email remains in history. The new draft uses the latest calendar availability.</p>
          </div>}
          {selectedSlot && <div className="lmp-review-slot">{providerName(selectedSlot.provider)} · {selectedSlot.account_name}<br />{meetingWindow(selectedSlot.start_at, selectedSlot.end_at, selectedSlot.time_zone)} · {selectedSlot.time_zone}</div>}
          {confirmError && <div className="lmp-provider-error" role="alert" style={{ marginTop: 10 }}>{confirmError}</div>}
          <div className="lmp-review-actions">
            <button className="lmp-secondary" type="button" disabled={confirming} onClick={() => { setReviewing(false); setConfirmError(null); }}>Back to availability</button>
            <button className="lmp-primary" type="button" disabled={!canEdit || !canViewEmails || confirming} onClick={() => void prepareMeetingOfferDraft()}>{confirming ? "Preparing draft…" : "Create appointment draft"}</button>
          </div>
        </div>}
        {!draftCreated && confirmError && !reviewing && <div className="lmp-outcome lmp-outcome-failure" role="alert">
          <span className="lmp-outcome-icon" aria-hidden="true">!</span>
          <div><h4>Appointment draft not prepared</h4><p>{confirmError}</p><div><button className="lmp-link-button" type="button" onClick={() => void prepareMeetingOfferDraft()}>Try again</button></div></div>
        </div>}
      </>}
    </div>
  </section>;
}

export default LeadMeetingPanel;
