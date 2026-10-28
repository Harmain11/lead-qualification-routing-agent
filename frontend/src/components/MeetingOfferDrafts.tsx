import { useEffect, useState } from "react";
import {
  approveAndSendLeadFollowUp,
  getLeadFollowUpHistory,
  syncLeadGmailConversation,
  updateLeadFollowUp,
} from "../api";
import type { FollowUpEmail } from "../types";

type Props = {
  leadId: string;
  leadEmail: string | null;
  active: boolean;
  canSend: boolean;
  onSent: () => void;
};

type DraftEdit = { subject: string; body: string };

export function MeetingOfferDrafts({
  leadId,
  leadEmail,
  active,
  canSend,
  onSent,
}: Props) {
  const [drafts, setDrafts] = useState<FollowUpEmail[]>([]);
  const [edits, setEdits] = useState<Record<string, DraftEdit>>({});
  const [busyId, setBusyId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    getLeadFollowUpHistory(leadId, controller.signal)
      .then((items) => {
        if (controller.signal.aborted) return;
        const pending = items.filter((item) =>
          item.kind.startsWith("meeting_options:")
          && item.status !== "sent"
          && item.status !== "superseded",
        );
        setDrafts(pending);
        setEdits((current) => {
          const next = { ...current };
          for (const item of pending) {
            if (!next[item.id]) {
              next[item.id] = { subject: item.subject, body: item.body ?? "" };
            }
          }
          return next;
        });
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "Meeting drafts could not be loaded.");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [active, leadId]);

  async function saveDraft(draft: FollowUpEmail) {
    const edit = edits[draft.id] ?? { subject: draft.subject, body: draft.body ?? "" };
    setBusyId(draft.id);
    setError(null);
    setNotice(null);
    try {
      const saved = await updateLeadFollowUp(leadId, draft.id, edit);
      setDrafts((current) => current.map((item) => item.id === saved.id ? saved : item));
      setNotice("Draft saved. It has not been sent.");
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "The meeting draft could not be saved.");
    } finally {
      setBusyId(null);
    }
  }

  async function sendDraft(draft: FollowUpEmail) {
    const edit = edits[draft.id] ?? { subject: draft.subject, body: draft.body ?? "" };
    const bookingLinkDraft = draft.generation_note === "Meeting booking link";
    setBusyId(draft.id);
    setError(null);
    setNotice(null);
    try {
      await updateLeadFollowUp(leadId, draft.id, edit);
      const sent = await approveAndSendLeadFollowUp(leadId, draft.id);
      if (sent.status !== "sent") {
        setDrafts((current) => current.map((item) => item.id === sent.id ? sent : item));
        setError(sent.error_message || "Gmail could not confirm delivery. Check Sent before retrying.");
        return;
      }
      setDrafts((current) => current.filter((item) => item.id !== sent.id));
      setNotice(bookingLinkDraft
        ? `Appointment booking link sent to ${sent.recipient || leadEmail || "the lead"}. The calendar will send an invitation after they book.`
        : draft.generation_note === "Manually selected calendar slot."
          ? `Meeting offer sent to ${sent.recipient || leadEmail || "the lead"}. Corral will book only after a clear reply with 1.`
          : `Meeting options sent to ${sent.recipient || leadEmail || "the lead"}. Corral will book only after a clear numbered reply.`);
      try {
        await syncLeadGmailConversation(leadId);
      } catch {
        setNotice(bookingLinkDraft
          ? "Appointment link sent. Email history could not be refreshed; do not resend."
          : "Meeting offer sent. Email history could not be refreshed; do not resend. Corral will book only after a clear reply with 1.");
      }
      onSent();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "The meeting offer could not be sent.");
      void getLeadFollowUpHistory(leadId).then((items) => {
        setDrafts(items.filter((item) =>
          item.kind.startsWith("meeting_options:")
          && item.status !== "sent"
          && item.status !== "superseded",
        ));
      }).catch(() => undefined);
    } finally {
      setBusyId(null);
    }
  }

  if (!active || (!loading && drafts.length === 0 && !error && !notice)) return null;

  return (
    <section className="meeting-reply-drafts" aria-label="Appointment drafts">
      <span className="eyebrow">Appointment draft · Gmail reply</span>
      {loading && <p role="status">Loading meeting drafts…</p>}
      {error && <div className="notice" role="alert">{error}</div>}
      {notice && <div className="notice" role="status">{notice}</div>}
      {drafts.map((draft) => {
        const edit = edits[draft.id] ?? { subject: draft.subject, body: draft.body ?? "" };
        const editable = canSend && draft.status === "draft";
        const busy = busyId === draft.id;
        const singleSelectedSlot = draft.generation_note === "Manually selected calendar slot.";
        const bookingLinkDraft = draft.generation_note === "Meeting booking link";
        return (
          <article className="meeting-reply-draft" key={draft.id}>
            <span className="eyebrow">Appointment draft</span>
            <p className="story-summary">
              {bookingLinkDraft
                ? "This personalized reply stays in the original Gmail thread. The lead chooses a time from the booking link, and the connected calendar creates the meeting."
                : singleSelectedSlot
                  ? "This selected time is option 1. Keep its line unchanged so the lead’s reply can book this exact slot."
                  : "The numbered times are saved with this draft. Corral books only after the lead clearly replies with one of those options."}
              {!bookingLinkDraft && " Sending does not book the meeting."}
            </p>
            <p><b>To:</b> {draft.recipient || leadEmail || "No email address"}</p>
            <label className="follow-up-field">
              Subject
              <input
                value={edit.subject}
                disabled={!editable || busy}
                onChange={(event) => setEdits((current) => ({
                  ...current,
                  [draft.id]: { ...edit, subject: event.target.value },
                }))}
              />
            </label>
            <label className="follow-up-field">
              Message
              <textarea
                rows={8}
                value={edit.body}
                disabled={!editable || busy}
                onChange={(event) => setEdits((current) => ({
                  ...current,
                  [draft.id]: { ...edit, body: event.target.value },
                }))}
              />
            </label>
            {draft.error_message && <div className="attention-callout">{draft.error_message}</div>}
            {draft.status === "delivery_uncertain" && (
              <p className="follow-up-status-copy">
                Delivery is uncertain. Check Gmail Sent before retrying.
              </p>
            )}
            {editable && (
              <div className="follow-up-actions">
                <button type="button" className="secondary-button" disabled={busy} onClick={() => void saveDraft(draft)}>
                  {busy ? "Saving…" : "Save draft"}
                </button>
                <button
                  type="button"
                  className="primary-button"
                  disabled={busy || !edit.subject.trim() || edit.body.trim().length < 10}
                  onClick={() => void sendDraft(draft)}
                >
                  {busy ? "Sending…" : bookingLinkDraft ? "Send appointment draft" : "Send meeting offer"}
                </button>
              </div>
            )}
          </article>
        );
      })}
    </section>
  );
}
