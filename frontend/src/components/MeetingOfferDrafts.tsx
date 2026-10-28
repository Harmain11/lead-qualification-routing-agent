import { useEffect, useState } from "react";
import {
  approveAndSendLeadFollowUp,
  deleteAppointmentEmailDraft,
  getLeadFollowUpHistory,
  syncLeadGmailConversation,
  updateLeadFollowUp,
} from "../api";
import type { FollowUpEmail, GmailSendRequest, GmailSendResult, ProviderConnection, WorkspaceEmailDraftInput } from "../types";
import { EmailComposer } from "./EmailComposer";

type Props = {
  leadId: string;
  leadEmail: string | null;
  active: boolean;
  canSend: boolean;
  accounts: ProviderConnection[];
  onSent: () => void;
};

function pendingAppointments(items: FollowUpEmail[]) {
  return items.filter((item) => item.kind.startsWith("meeting_options:")
    && item.status !== "sent" && item.status !== "superseded");
}

export function MeetingOfferDrafts({ leadId, leadEmail, active, canSend, accounts, onSent }: Props) {
  const [drafts, setDrafts] = useState<FollowUpEmail[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    getLeadFollowUpHistory(leadId, controller.signal)
      .then((items) => { if (!controller.signal.aborted) setDrafts(pendingAppointments(items)); })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Appointment drafts could not be loaded.");
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [active, leadId]);

  async function saveDraft(draft: FollowUpEmail, payload: WorkspaceEmailDraftInput) {
    const saved = await updateLeadFollowUp(leadId, draft.id, {
      subject: payload.subject,
      body: payload.body,
      cc: payload.cc,
      bcc: payload.bcc,
      html_body: payload.html_body,
      attachments: payload.attachments,
    });
    setDrafts((current) => current.map((item) => item.id === saved.id ? saved : item));
  }

  async function sendDraft(draft: FollowUpEmail, payload: GmailSendRequest): Promise<GmailSendResult> {
    await saveDraft(draft, {
      connection_id: payload.connection_id,
      recipient: payload.recipient,
      cc: payload.cc ?? "",
      bcc: payload.bcc ?? "",
      subject: payload.subject,
      body: payload.body,
      html_body: payload.html_body ?? null,
      attachments: payload.attachments ?? [],
      lead_id: leadId,
      thread_id: payload.thread_id ?? null,
      in_reply_to: payload.in_reply_to ?? null,
    });
    const sent = await approveAndSendLeadFollowUp(leadId, draft.id);
    if (sent.status !== "sent" || !sent.external_message_id) {
      setDrafts((current) => current.map((item) => item.id === sent.id ? sent : item));
      throw new Error(sent.error_message || "Gmail could not confirm delivery. Check Sent before retrying.");
    }
    setDrafts((current) => current.filter((item) => item.id !== sent.id));
    setNotice(`Appointment email sent to ${sent.recipient || leadEmail || "the lead"}. The calendar will show the meeting after they book.`);
    try {
      await syncLeadGmailConversation(leadId);
    } catch {
      setNotice("Appointment email sent. Gmail history could not refresh; do not resend.");
    }
    onSent();
    return {
      message_id: sent.external_message_id,
      thread_id: sent.thread_id,
      sender: accounts.find((account) => account.id === sent.connection_id)?.account_name ?? "Gmail",
      recipient: sent.recipient,
      sent_at: sent.sent_at ?? new Date().toISOString(),
    };
  }

  if (!active || (!loading && drafts.length === 0 && !error && !notice)) return null;

  return <section className="meeting-reply-drafts" aria-label="Appointment drafts">
    {loading && <p role="status">Loading appointment drafts…</p>}
    {error && <div className="notice" role="alert">{error}</div>}
    {notice && <div className="notice" role="status">{notice}</div>}
    {drafts.map((draft) => {
      const replyReady = Boolean(draft.thread_id && draft.in_reply_to && draft.connection_id);
      return <article className="meeting-reply-draft" key={draft.id}>
        <div className="meeting-reply-intro">
          <span className="eyebrow">Appointment draft · Gmail reply</span>
          <h3>Review appointment email</h3>
          <p>Edit the message, add formatting or attachments, then save it or send it in the original Gmail conversation.</p>
        </div>
        {draft.status === "delivery_uncertain" && <div className="attention-callout">Check Gmail Sent before retrying this draft.</div>}
        {draft.error_message && <div className="attention-callout">{draft.error_message}</div>}
        {draft.status === "draft" && <EmailComposer
          key={`${draft.id}:${draft.status}`}
          accounts={accounts}
          canSend={canSend}
          leadId={leadId}
          seed={{
            recipient: draft.recipient || leadEmail || "",
            subject: draft.subject,
            body: draft.body ?? "",
            cc: draft.cc,
            bcc: draft.bcc,
            htmlBody: draft.html_body ?? undefined,
            attachments: draft.attachments,
            connectionId: draft.connection_id ?? undefined,
            threadId: draft.thread_id ?? undefined,
            inReplyTo: draft.in_reply_to ?? undefined,
          }}
          sendBlockedReason={replyReady ? undefined : "Refresh the original Gmail conversation before sending this appointment reply."}
          headingLabel="Gmail appointment reply"
          headingTitle="Write your appointment email"
          onSaveOverride={(payload) => saveDraft(draft, payload)}
          onSendOverride={(payload) => sendDraft(draft, payload)}
          onDeleteOverride={async () => {
            await deleteAppointmentEmailDraft(leadId, draft.id);
            setDrafts((current) => current.filter((item) => item.id !== draft.id));
          }}
          sendLabel="Send appointment email"
        />}
      </article>;
    })}
  </section>;
}
