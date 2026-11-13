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
... (+379 lines) [see remaining: rtk proxy git show HEAD:frontend/src/components/LeadMeetingPanel.tsx | tail -n +181]
