import { useEffect, useState, type FormEvent } from "react";
import { cancelLeadMeeting, getLeadManagedMeeting, rescheduleLeadMeeting } from "../api";
import type { ManagedMeeting } from "../types";
import type { LeadMeetingIndicator } from "./LeadMeetingPanel";
import { confirmAction } from "../v3/ui/confirm";

type Props = {
  leadId: string;
  canEdit: boolean;
  refreshToken?: number;
  onChanged: () => void;
  onStatusChange?: (status: LeadMeetingIndicator | null) => void;
};

function formatTime(value: string) {
  return new Date(value).toLocaleString(undefined, { dateStyle: "full", timeStyle: "short" });
}

export function LeadMeetingManagement({ leadId, canEdit, refreshToken, onChanged, onStatusChange }: Props) {
  const [meeting, setMeeting] = useState<ManagedMeeting | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState(false);
  const [action, setAction] = useState<"cancel" | "reschedule" | null>(null);
  const [reason, setReason] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    getLeadManagedMeeting(leadId, controller.signal).then((value) => {
      if (controller.signal.aborted) return;
      setMeeting(value);
      onStatusChange?.(value ? { kind: "booked", bookedAt: value.start_at } : null);
    }).catch((reason: unknown) => {
      if (controller.signal.aborted) return;
      setMeeting(null);
      setError(reason instanceof Error ? reason.message : "The calendar booking could not be verified.");
      onStatusChange?.({ kind: "needs_attention", bookedAt: null });
    });
    return () => controller.abort();
  }, [leadId, refreshToken, refresh]);

  async function cancel(event: FormEvent) {
    event.preventDefault();
    if (!meeting || !reason.trim() || busy) return;
    if (!(await confirmAction({ title: `Cancel the ${formatTime(meeting.start_at)} meeting?`, message: "The calendar provider may notify every attendee.", confirmLabel: "Cancel meeting", cancelLabel: "Keep meeting", tone: "danger" }))) return;
    setBusy(true);
    setError(null);
    try {
      const result = await cancelLeadMeeting(leadId, meeting.id, reason.trim());
      setMeeting(null);
      setAction(null);
      setNotice(`${result.message} Reason saved: ${reason.trim()}`);
      onStatusChange?.(null);
      onChanged();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Calendar cancellation was not confirmed.");
    } finally {
      setBusy(false);
    }
  }

  async function reschedule(event: FormEvent) {
    event.preventDefault();
    if (!meeting || busy) return;
    const startAt = new Date(start);
    const endAt = new Date(end);
    if (!Number.isFinite(startAt.getTime()) || !Number.isFinite(endAt.getTime()) || endAt <= startAt) {
      setError("Choose a valid start and end time.");
      return;
    }
    if (!(await confirmAction({ title: `Move the meeting to ${formatTime(startAt.toISOString())}?`, message: "The calendar provider may notify every attendee.", confirmLabel: "Move meeting" }))) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await rescheduleLeadMeeting(
        leadId, meeting.id, startAt.toISOString(), endAt.toISOString(),
        Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
      );
      setMeeting(updated);
      setAction(null);
      setNotice(`Google Calendar confirmed the new time: ${formatTime(updated.start_at)}.`);
      onStatusChange?.({ kind: "booked", bookedAt: updated.start_at });
      onChanged();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Calendar rescheduling was not confirmed.");
    } finally {
      setBusy(false);
    }
  }

  if (!meeting && !error && !notice) return null;
  return <section className="meeting-management" aria-label="Manage booked meeting">
    <div className="meeting-management-head">
      <div><span className="eyebrow">Verified calendar booking</span><h3>{meeting ? "Meeting booked" : "Meeting status"}</h3></div>
      <button className="button-quiet" type="button" onClick={() => setRefresh((value) => value + 1)} disabled={busy}>Refresh status</button>
    </div>
    {meeting && <>
      <p className="meeting-management-time">{formatTime(meeting.start_at)}</p>
      <p className="meeting-management-meta">{meeting.provider === "calendly" ? "Calendly" : "Google Calendar"} · {meeting.title}</p>
      {meeting.event_url?.startsWith("https://") && <a href={meeting.event_url} target="_blank" rel="noreferrer">Open calendar event ↗</a>}
      {canEdit && <div className="meeting-management-actions">
        {meeting.provider === "calendly" ? (
          meeting.reschedule_url && <a className="secondary-button" href={meeting.reschedule_url} target="_blank" rel="noreferrer">Reschedule in Calendly ↗</a>
        ) : <button className="secondary-button" type="button" onClick={() => { setAction(action === "reschedule" ? null : "reschedule"); setError(null); }}>Reschedule</button>}
        {meeting.can_cancel_here
          ? <button className="secondary-button" type="button" onClick={() => { setAction(action === "cancel" ? null : "cancel"); setError(null); }}>Cancel meeting</button>
          : meeting.cancel_url && <a className="secondary-button" href={meeting.cancel_url} target="_blank" rel="noreferrer">Cancel invitee in Calendly ↗</a>}
      </div>}
      {meeting.provider === "calendly" && <small>After changing the meeting in Calendly, select Refresh status here to verify the result.</small>}
      {action === "cancel" && <form className="meeting-management-form" onSubmit={(event) => void cancel(event)}>
        <label>Reason for cancellation<textarea value={reason} onChange={(event) => setReason(event.target.value)} minLength={3} maxLength={500} required placeholder="Explain why this meeting needs to be cancelled" /></label>
        <div><button type="button" className="button-quiet" onClick={() => setAction(null)}>Keep meeting</button><button type="submit" className="primary-button" disabled={busy || reason.trim().length < 3}>{busy ? "Cancelling…" : "Confirm cancellation"}</button></div>
      </form>}
      {action === "reschedule" && <form className="meeting-management-form" onSubmit={(event) => void reschedule(event)}>
        <p>Choose the new start and end in your device’s timezone.</p>
        <label>New start<input type="datetime-local" value={start} onChange={(event) => setStart(event.target.value)} required /></label>
        <label>New end<input type="datetime-local" value={end} onChange={(event) => setEnd(event.target.value)} required /></label>
        <div><button type="button" className="button-quiet" onClick={() => setAction(null)}>Keep current time</button><button type="submit" className="primary-button" disabled={busy || !start || !end}>{busy ? "Rescheduling…" : "Confirm new time"}</button></div>
      </form>}
    </>}
    {error && <p className="meeting-management-alert" role="alert">{error} No meeting change was confirmed.</p>}
    {notice && <p className="meeting-management-notice" role="status">{notice}</p>}
  </section>;
}
