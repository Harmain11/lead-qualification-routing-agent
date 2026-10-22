import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  approveAndSendLeadFollowUp,
  chatWithLeadAssistant,
  deleteLead,
  generateLeadFollowUp,
  getLead,
  getLeadClosingOptions,
  getLeadFollowUp,
  listIntegrations,
  reevaluateLead,
  saveLeadNote,
  sendLeadToCrm,
  syncLeadGmailConversation,
  updateLeadFollowUp,
  updateLeadProfile,
  updateLeadProgress,
  type LeadAssistantAction,
  type LeadAssistantReply,
} from "../api";
import type { FollowUpEmail, Lead } from "../types";
import type { LeadDetailSection } from "../leadSections";
import { OPEN_DIALER_PHONE_EVENT } from "./dialerPhoneConfig";
import { createClientId } from "../clientIds";
import { assistantConversation } from "../assistantConversation";
import "./leadAssistant.css";

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  action?: LeadAssistantAction;
  busy?: boolean;
  done?: boolean;
  error?: string;
  notice?: string;
  preview?: FollowUpEmail;
  emailSubject?: string;
  emailBody?: string;
  actionResult?: string;
};

type ClosingOption = { user_id: string; display_name: string };
type CrmOption = { provider: string; name: string };
type ClosingSelection = { conversionType: "" | "project" | "sale"; creditedUserId: string; revenue: string };

const suggestions = [
  "Summarize this lead and suggest a next step",
  "What details are still missing?",
  "Write and send a follow-up email",
  "I want to call this lead",
];
const fieldLabels: Record<string, string> = {
  full_name: "Name",
  email: "Email",
  phone: "Phone",
  job_title: "Job title",
  company_name: "Company",
  industry: "Industry",
  employee_count: "Team size",
  annual_revenue: "Annual revenue",
  geography: "Geography",
  budget: "Budget",
  product_or_service: "Requested service",
};
const stageLabels: Record<string, string> = {
  contacted: "Contacted",
  responded: "Responded",
  meeting_scheduled: "Meeting scheduled",
  proposal_sent: "Proposal sent",
  converted: "Converted",
  lost: "Lost",
};
const crmName = (provider: string) =>
  provider === "hubspot" ? "HubSpot" : provider === "gohighlevel" ? "GoHighLevel" : provider[0]?.toUpperCase() + provider.slice(1);
const id = createClientId;

export function LeadAssistantPanel({
  lead,
  canEdit,
  canCrmSync,
  canViewEmail,
  canUseAgent,
  dialerPhoneConnectionId,
  onUpdated,
  onDeleted,
  onSectionChange,
}: {
  lead: Lead;
  canEdit: boolean;
  canCrmSync: boolean;
  canViewEmail: boolean;
  canUseAgent: boolean;
  dialerPhoneConnectionId: string | null;
  onUpdated: (lead: Lead) => void;
  onDeleted: (leadId: string) => void;
  onSectionChange: (section: LeadDetailSection) => void;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [quickActionNotice, setQuickActionNotice] = useState("");
  const [closingOptions, setClosingOptions] = useState<ClosingOption[]>([]);
  const [crmOptions, setCrmOptions] = useState<CrmOption[]>([]);
  const [selectedCrm, setSelectedCrm] = useState(lead.crm_sync?.provider || "");
  const [closingSelection, setClosingSelection] = useState<Record<string, ClosingSelection>>({});
  const transcriptRef = useRef<HTMLDivElement>(null);
  const actionInFlightRef = useRef(new Set<string>());

  useEffect(() => {
    setMessages([]);
    setDraft("");
    setError("");
    setQuickActionNotice("");
  }, [lead.id]);

  useEffect(() => {
    transcriptRef.current?.scrollTo({ top: transcriptRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy]);

  useEffect(() => {
    if (!canEdit) return;
    getLeadClosingOptions().then(setClosingOptions).catch(() => setClosingOptions([]));
  }, [canEdit, lead.id]);

  useEffect(() => {
    if (!canCrmSync) return;
    listIntegrations().then((cards) => {
      const options = cards
        .filter((card) => card.category.toLowerCase() === "crm" && card.directionality.manual_outbound)
        .flatMap((card) => {
          const connections = card.connections.length ? card.connections : card.connection ? [card.connection] : [];
          return connections.some((connection) => connection.is_active && connection.verification_status === "verified")
            ? [{ provider: card.provider, name: card.display_name }]
            : [];
        });
      setCrmOptions(options);
      setSelectedCrm((current) =>
        options.some((option) => option.provider === current)
          ? current
          : options.length === 1 ? options[0].provider : "",
      );
    }).catch(() => setCrmOptions([]));
  }, [canCrmSync, lead.id]);

  async function submit(event?: FormEvent, text = draft) {
    event?.preventDefault();
    const content = text.trim();
    if (!content || busy || !canUseAgent) return;
    setQuickActionNotice("");
    const userMessage: ChatMessage = { id: id(), role: "user", content };
    const history = [...messages, userMessage];
    setMessages(history);
    setDraft("");
    setBusy(true);
    setError("");
    try {
      const reply: LeadAssistantReply = await chatWithLeadAssistant(
        lead.id,
        assistantConversation(history),
      );
      const phoneActionUnavailable =
        reply.action.kind === "open_dialer" && !dialerPhoneConnectionId;
      setMessages((current) => [...current, {
        id: id(),
        role: "assistant",
        content: phoneActionUnavailable
          ? `${reply.reply} There isn’t an active embedded phone selected, so I can’t open it here yet.`
          : reply.reply,
        action: phoneActionUnavailable
          ? { ...reply.action, kind: "none" }
          : reply.action,
      }]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The assistant could not answer. Try again.");
    } finally {
      setBusy(false);
    }
  }

  async function copyLeadPhone() {
    const phone = lead.phone?.trim();
    if (!phone) return;
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(phone);
      setQuickActionNotice("Phone number copied.");
    } catch {
      setQuickActionNotice("Copy isn’t available here. Select the phone number to copy it.");
    }
  }

  function openLeadPhone() {
    if (!dialerPhoneConnectionId || !lead.phone?.trim()) {
      setQuickActionNotice("Select an active embedded phone and save a lead number before opening the phone.");
      return;
    }
    window.dispatchEvent(new CustomEvent(OPEN_DIALER_PHONE_EVENT, {
      detail: { connectionId: dialerPhoneConnectionId },
    }));
    setQuickActionNotice("Phone opened. Enter the saved number in its keypad; no call has been started.");
  }

  function startEmailDraft() {
    void submit(
      undefined,
      `Draft a follow-up email to ${lead.full_name} using the information in this lead. Show it here so I can review and edit it before sending.`,
    );
  }

  function changeMessage(messageId: string, changes: Partial<ChatMessage>) {
    setMessages((current) => current.map((message) => message.id === messageId ? { ...message, ...changes } : message));
  }

  function setCloseField(messageId: string, field: keyof ClosingSelection, value: string) {
    setClosingSelection((current) => ({
      ...current,
      [messageId]: {
        conversionType: current[messageId]?.conversionType ?? "",
        creditedUserId: current[messageId]?.creditedUserId ?? "",
        revenue: current[messageId]?.revenue ?? "",
        [field]: value,
      },
    }));
  }

  async function reviewFollowUp(message: ChatMessage) {
    if (!canEdit) return;
    changeMessage(message.id, { busy: true, error: undefined });
    try {
      let followUp = await getLeadFollowUp(lead.id);
      if (!followUp || followUp.status === "sent") followUp = await generateLeadFollowUp(lead.id);
      changeMessage(message.id, {
        busy: false,
        preview: followUp,
        emailSubject: followUp.subject,
        emailBody: followUp.body || "",
      });
    } catch (reason) {
      changeMessage(message.id, {
        busy: false,
        error: reason instanceof Error ? reason.message : "The email draft could not be loaded.",
      });
    }
  }

  async function confirmFollowUpSend(message: ChatMessage) {
    const followUp = message.preview;
    if (
      !canEdit ||
      !followUp ||
      followUp.status !== "draft" ||
      message.done ||
      message.busy ||
      message.emailSubject !== followUp.subject ||
      message.emailBody !== (followUp.body || "") ||
      actionInFlightRef.current.has(message.id)
    ) return;
    actionInFlightRef.current.add(message.id);
    changeMessage(message.id, { busy: true, error: undefined });
    try {
      const sent = await approveAndSendLeadFollowUp(lead.id, followUp.id);
      changeMessage(message.id, {
        busy: false,
        done: sent.status === "sent",
        preview: sent,
        actionResult: sent.status === "sent"
          ? `Email sent to ${sent.recipient}. Subject: ${sent.subject}.`
          : undefined,
        error: sent.status === "sent"
          ? undefined
          : sent.error_message || "Gmail could not confirm that the email was sent.",
      });
      if (sent.status === "sent") void refreshLeadAfterAction(message.id);
    } catch (reason) {
      changeMessage(message.id, {
        busy: false,
        error: reason instanceof Error ? reason.message : "The email could not be sent.",
      });
    } finally {
      actionInFlightRef.current.delete(message.id);
    }
  }

  function editFollowUpDraft(
    messageId: string,
    field: "emailSubject" | "emailBody",
    value: string,
  ) {
    changeMessage(messageId, { [field]: value });
  }

  async function saveFollowUpDraft(message: ChatMessage) {
    const followUp = message.preview;
    const subject = message.emailSubject?.trim() || "";
    const body = message.emailBody || "";
    if (
      !canEdit ||
      !followUp ||
      followUp.status !== "draft" ||
      !subject ||
      body.trim().length < 10 ||
      actionInFlightRef.current.has(message.id)
    ) return;
    actionInFlightRef.current.add(message.id);
    changeMessage(message.id, { busy: true, error: undefined, notice: undefined });
    try {
      const saved = await updateLeadFollowUp(lead.id, followUp.id, { subject, body });
      changeMessage(message.id, {
        busy: false,
        preview: saved,
        emailSubject: saved.subject,
        emailBody: saved.body || "",
        notice: "Draft saved. Review it here, then choose Send follow-up when you’re ready.",
      });
    } catch (reason) {
      changeMessage(message.id, {
        busy: false,
        error: reason instanceof Error ? reason.message : "The draft could not be saved.",
      });
    } finally {
      actionInFlightRef.current.delete(message.id);
    }
  }

  async function refreshLeadAfterAction(messageId: string) {
    try {
      onUpdated(await getLead(lead.id));
    } catch {
      changeMessage(messageId, {
        notice: "The action completed, but the latest lead details could not be refreshed. Reopen the lead to see the saved result.",
      });
    }
  }

  async function executeAction(message: ChatMessage) {
    const action = message.action;
    if (!action || action.kind === "none" || message.busy || message.done || actionInFlightRef.current.has(message.id)) return;
    if (["update_profile", "add_note", "record_progress", "draft_email", "send_follow_up", "reevaluate_lead"].includes(action.kind) && !canEdit) {
      changeMessage(message.id, { error: "You need lead-edit access to make this change." });
      return;
    }
    if (action.kind === "sync_crm" && !canCrmSync) {
      changeMessage(message.id, { error: "You need CRM sync access to send this lead." });
      return;
    }
    if (action.kind === "open_email" && !canViewEmail) {
      changeMessage(message.id, { error: "You do not have access to this lead’s email section." });
      return;
    }
    if (action.kind === "refresh_email" && !canViewEmail) {
      changeMessage(message.id, { error: "You do not have access to this lead’s email section." });
      return;
    }
    actionInFlightRef.current.add(message.id);
    changeMessage(message.id, { busy: true, error: undefined });
    try {
      if (action.kind === "reevaluate_lead") {
        if (!window.confirm(
          `Re-evaluate ${lead.full_name}'s qualification and routing using its current profile and recorded history?`,
        )) return;
        const updated = await reevaluateLead(lead.id);
        onUpdated(updated);
        changeMessage(message.id, { done: true });
      } else if (action.kind === "update_profile") {
        const updates = Object.fromEntries(
          Object.entries(action.profile_updates).filter(([, value]) => value !== null && value !== undefined),
        ) as LeadAssistantAction["profile_updates"];
        for (const field of action.clear_fields) {
          (updates as Record<string, unknown>)[field] = null;
        }
        if (!Object.keys(updates).length) throw new Error("No lead fields were provided to update.");
        const updated = await updateLeadProfile(lead.id, updates);
        onUpdated(updated);
        changeMessage(message.id, { done: true });
      } else if (action.kind === "add_note") {
        if (!action.note?.trim()) throw new Error("The assistant did not provide a note to save.");
        await saveLeadNote(lead.id, action.note);
        changeMessage(message.id, { done: true });
      } else if (action.kind === "record_progress") {
        if (!action.progress_stage || !action.progress_note?.trim()) {
          throw new Error("A progress stage and note are required.");
        }
        const selection = closingSelection[message.id];
        const converted = action.progress_stage === "converted";
        const conversionType = selection?.conversionType || action.conversion_type;
        const creditedUserId = selection?.creditedUserId || action.credited_user_id || "";
        if (converted && (!conversionType || !creditedUserId)) {
          throw new Error("Choose Project or Sale and select the credited team member before recording a win.");
        }
        const revenue = selection?.revenue ? Number(selection.revenue) : action.closed_revenue ?? undefined;
        await updateLeadProgress(lead.id, {
          stage: action.progress_stage,
          note: action.progress_note,
          ...(converted ? {
            conversion_type: conversionType as "project" | "sale",
            credited_user_id: creditedUserId,
            ...(revenue !== undefined ? { closed_revenue: revenue } : {}),
          } : {}),
        });
        changeMessage(message.id, { done: true });
        void refreshLeadAfterAction(message.id);
      } else if (action.kind === "draft_email") {
        let generated = await generateLeadFollowUp(lead.id);
        if (action.email_subject?.trim() || action.email_body?.trim()) {
          generated = await updateLeadFollowUp(lead.id, generated.id, {
            subject: action.email_subject?.trim() || generated.subject,
            body: action.email_body?.trim() || generated.body || "",
          });
        }
        changeMessage(message.id, {
          done: false,
          preview: generated,
          emailSubject: generated.subject,
          emailBody: generated.body || "",
          notice: "Draft created. Review or edit it here. It will not be sent until you choose Send follow-up.",
        });
      } else if (action.kind === "send_follow_up") {
        await reviewFollowUp(message);
      } else if (action.kind === "refresh_email") {
        if (!canEdit) throw new Error("You need lead-edit access to refresh Gmail for this lead.");
        await syncLeadGmailConversation(lead.id);
        changeMessage(message.id, { done: true });
        void refreshLeadAfterAction(message.id);
        onSectionChange("email");
      } else if (action.kind === "sync_crm") {
        const provider = crmOptions.some((option) => option.provider === action.crm_provider)
          ? action.crm_provider!
          : selectedCrm;
        if (!provider) throw new Error("Select a verified CRM connection first.");
        if (!window.confirm(`Sync ${lead.full_name} to ${crmName(provider)} now?`)) return;
        changeMessage(message.id, { busy: true, error: undefined });
        await sendLeadToCrm(lead.id, provider);
        changeMessage(message.id, { busy: false, done: true });
        void refreshLeadAfterAction(message.id);
      } else if (action.kind === "delete_lead") {
        if (!canEdit) throw new Error("You need lead-edit access to delete this lead.");
        if (!window.confirm(`Delete ${lead.full_name} from this workspace? A later matching inquiry may be imported again.`)) return;
        await deleteLead(lead.id);
        onDeleted(lead.id);
      } else if (action.kind === "open_email") {
        onSectionChange("email");
        changeMessage(message.id, { done: true });
      } else if (action.kind === "open_meetings") {
        onSectionChange("follow-ups");
        changeMessage(message.id, { done: true });
      } else if (action.kind === "open_dialer") {
        if (!dialerPhoneConnectionId || !lead.phone?.trim()) {
          throw new Error("No active embedded phone is selected.");
        }
        window.dispatchEvent(new CustomEvent(OPEN_DIALER_PHONE_EVENT, {
          detail: { connectionId: dialerPhoneConnectionId },
        }));
        changeMessage(message.id, {
          done: true,
          actionResult: `Phone opened. Dial ${lead.phone} from its keypad. Corral has not placed a call.`,
        });
      } else if (action.kind === "open_closing") {
        onSectionChange("closing");
        changeMessage(message.id, { done: true });
      } else if (action.kind === "open_history") {
        onSectionChange("history");
        changeMessage(message.id, { done: true });
      }
    } catch (reason) {
      changeMessage(message.id, {
        busy: false,
        error: reason instanceof Error ? reason.message : "That lead action could not be completed.",
      });
    } finally {
      actionInFlightRef.current.delete(message.id);
      changeMessage(message.id, { busy: false });
    }
  }

  function actionTitle(action: LeadAssistantAction) {
    switch (action.kind) {
      case "update_profile": return "Update lead details";
      case "add_note": return "Save team note";
      case "record_progress": return "Record lead progress";
      case "draft_email": return "Create follow-up draft";
      case "send_follow_up": return "Review and send follow-up";
      case "refresh_email": return "Refresh Gmail conversation";
      case "sync_crm": return "Sync to CRM";
      case "delete_lead": return "Delete this lead";
      case "reevaluate_lead": return "Re-evaluate qualification and routing";
      case "open_email": return "Open email";
      case "open_meetings": return "Review meeting times";
      case "open_dialer": return "Open phone to call";
      case "open_closing": return "Open comments and closing";
      case "open_history": return "Open lead history";
      default: return "";
    }
  }

  function renderAction(message: ChatMessage) {
    const action = message.action;
    if (!action || action.kind === "none") return null;
    const changes = Object.entries(action.profile_updates).filter(([, value]) => value !== null && value !== undefined);
    const selection = closingSelection[message.id] ?? {
      conversionType: action.conversion_type ?? "",
      creditedUserId: action.credited_user_id ?? "",
      revenue: action.closed_revenue?.toString() ?? "",
    };
    const needsCredit = action.progress_stage === "converted";
    const editRequired = ["update_profile", "add_note", "record_progress", "draft_email", "send_follow_up", "refresh_email", "delete_lead", "reevaluate_lead"].includes(action.kind);
    return <details className="lead-agent-action" open={!message.done || message.preview?.status === "draft"}>
      <summary className="lead-agent-action-title"><span aria-hidden="true">↳</span><strong>{actionTitle(action)}</strong>{message.done && <small>{action.kind === "open_dialer" ? "Phone opened · not called" : action.kind.startsWith("open_") ? "Opened" : "Completed"}</small>}</summary>
      {action.kind === "update_profile" && <dl className="lead-agent-changes">
        {changes.map(([field, value]) => <div key={field}><dt>{fieldLabels[field] || field}</dt><dd>{String(value)}</dd></div>)}
        {action.clear_fields.map((field) => <div key={`clear-${field}`}><dt>{fieldLabels[field] || field}</dt><dd>Clear this field</dd></div>)}
      </dl>}
      {action.kind === "add_note" && <p className="lead-agent-action-copy">{action.note}</p>}
      {action.kind === "open_dialer" && <p className="lead-agent-action-copy">Saved number: <b>{lead.phone || "Not available"}</b>. Opens the phone only; you still choose whether to place the call.</p>}
      {action.kind === "record_progress" && <>
        <p className="lead-agent-action-copy"><b>{stageLabels[action.progress_stage || ""] || "Progress update"}</b>{action.progress_note && <> · {action.progress_note}</>}</p>
        {needsCredit && !message.done && <div className="lead-agent-closing-fields">
          <label>Win type
            <select value={selection.conversionType} onChange={(event) => setCloseField(message.id, "conversionType", event.target.value)}>
              <option value="">Choose Project or Sale</option><option value="project">Project</option><option value="sale">Sale</option>
            </select>
          </label>
          <label>Credited member
            <select value={selection.creditedUserId} onChange={(event) => setCloseField(message.id, "creditedUserId", event.target.value)}>
              <option value="">Choose a team member</option>
              {closingOptions.map((member) => <option key={member.user_id} value={member.user_id}>{member.display_name}</option>)}
            </select>
          </label>
          <label>Revenue (optional)
            <input type="number" min="0" step="0.01" value={selection.revenue} onChange={(event) => setCloseField(message.id, "revenue", event.target.value)} />
          </label>
        </div>}
      </>}
      {action.kind === "sync_crm" && !message.done && <label className="lead-agent-provider-choice">CRM connection
        <select value={selectedCrm} onChange={(event) => setSelectedCrm(event.target.value)}>
          <option value="">Choose a verified connection</option>
          {crmOptions.map((option) => <option key={option.provider} value={option.provider}>{option.name}</option>)}
        </select>
      </label>}
      {message.preview && <div className="lead-agent-email-editor">
        <p className="lead-agent-email-recipient"><b>To</b> {message.preview.recipient}</p>
        {message.preview.status === "sent" ? (
          <>
            <p className="lead-agent-email-status">
              Sent {message.preview.sent_at ? new Date(message.preview.sent_at).toLocaleString() : "successfully"}.
            </p>
            <p><b>Subject:</b> {message.preview.subject}</p>
            <div className="lead-agent-email-body">{message.preview.body}</div>
          </>
        ) : message.preview.status === "draft" ? (
          <>
            <label>
              Subject
              <input
                type="text"
                maxLength={180}
                value={message.emailSubject ?? message.preview.subject}
                onChange={(event) => editFollowUpDraft(message.id, "emailSubject", event.target.value)}
                disabled={message.busy}
              />
            </label>
            <label>
              Message
              <textarea
                rows={8}
                maxLength={5000}
                value={message.emailBody ?? message.preview.body ?? ""}
                onChange={(event) => editFollowUpDraft(message.id, "emailBody", event.target.value)}
                disabled={message.busy}
              />
            </label>
            {message.preview.rationale && <p className="lead-agent-email-meta"><b>Why this draft:</b> {message.preview.rationale}</p>}
            {message.preview.generation_note && <p className="lead-agent-email-meta">{message.preview.generation_note}</p>}
            <p className="lead-agent-email-meta">This email stays in Corral until you choose <b>Send follow-up</b>.</p>
          </>
        ) : (
          <p className="lead-agent-email-status">
            {message.preview.error_message || "This draft is no longer available to edit or send."}
          </p>
        )}
      </div>}
      {message.actionResult && <p className="lead-agent-notice" role="status">{message.actionResult}</p>}
      {message.error && <p className="lead-agent-error" role="alert">{message.error}</p>}
      {message.notice && <p className="lead-agent-notice" role="status">{message.notice}</p>}
      {message.preview?.status === "draft" && !message.done && <div className="lead-agent-action-buttons">
        <button
          className="secondary-button"
          type="button"
          disabled={
            Boolean(message.busy) ||
            !(message.emailSubject ?? message.preview.subject).trim() ||
            (message.emailBody ?? message.preview.body ?? "").trim().length < 10 ||
            (
              (message.emailSubject ?? message.preview.subject) === message.preview.subject &&
              (message.emailBody ?? message.preview.body ?? "") === (message.preview.body || "")
            )
          }
          onClick={() => void saveFollowUpDraft(message)}
        >
          {message.busy ? "Saving…" : "Save draft changes"}
        </button>
        <button
          className="primary-button"
          type="button"
          disabled={
            Boolean(message.busy) ||
            !(message.emailSubject ?? message.preview.subject).trim() ||
            (message.emailBody ?? message.preview.body ?? "").trim().length < 10 ||
            (message.emailSubject ?? message.preview.subject) !== message.preview.subject ||
            (message.emailBody ?? message.preview.body ?? "") !== (message.preview.body || "")
          }
          onClick={() => void confirmFollowUpSend(message)}
        >
          {message.busy ? "Sending…" : "Send follow-up"}
        </button>
      </div>}
      {!message.done && !message.preview && <div className="lead-agent-action-buttons">
        <button
            className={["sync_crm", "delete_lead", "send_follow_up", "reevaluate_lead"].includes(action.kind) ? "secondary-button" : "primary-button"}
            type="button"
            disabled={message.busy || (editRequired && !canEdit) || (action.kind === "sync_crm" && (!canCrmSync || !selectedCrm)) || (action.kind === "open_email" && !canViewEmail) || (needsCredit && (!selection.conversionType || !selection.creditedUserId))}
            onClick={() => void executeAction(message)}
          >
            {message.busy ? "Working…" : editRequired && !canEdit ? "Lead-edit access required" : action.kind === "sync_crm" && !canCrmSync ? "CRM-sync access required" : action.kind === "sync_crm" ? "Confirm CRM sync" : action.kind === "delete_lead" ? "Confirm delete" : action.kind === "reevaluate_lead" ? "Confirm re-evaluation" : action.kind === "send_follow_up" ? "Review draft" : action.kind === "draft_email" ? "Create draft and review" : action.kind === "refresh_email" ? "Refresh Gmail" : action.kind === "record_progress" ? "Save progress" : action.kind === "update_profile" ? "Apply changes" : action.kind === "add_note" ? "Save note" : action.kind === "open_meetings" ? "Review available times" : action.kind === "open_email" ? (canViewEmail ? "Open email section" : "Email access required") : action.kind === "open_closing" ? "Open closing section" : action.kind === "open_history" ? "Open history" : "Continue"}
          </button>
      </div>}
    </details>;
  }

  return <section className="lead-agent" aria-label={`Assistant for ${lead.full_name}`}>
    <header className="lead-agent-heading">
      <div className="lead-agent-mark" aria-hidden="true">CA</div>
      <div><span className="eyebrow">Lead assistant</span><h2>Work with {lead.full_name}</h2><p>Ask Corral to take an action, then review it here before anything is sent or changed.</p></div>
    </header>
    <section className="lead-agent-quick-actions" aria-label="Quick lead actions">
      <div className="lead-agent-phone-card">
        <div className="lead-agent-phone-copy">
          <span className="eyebrow">Phone number</span>
          <strong>{lead.phone || "No number saved"}</strong>
        </div>
        <div className="lead-agent-quick-buttons">
          {lead.phone && <button type="button" className="secondary-button" onClick={() => void copyLeadPhone()}>Copy number</button>}
          {lead.phone && dialerPhoneConnectionId && <button type="button" className="secondary-button" onClick={openLeadPhone}>Open phone</button>}
          {!lead.phone && canEdit && <button type="button" className="secondary-button" onClick={() => {
            setDraft(`Add a phone number for ${lead.full_name}: `);
            window.requestAnimationFrame(() => document.getElementById("lead-agent-input")?.focus());
          }}>Add number with Corral</button>}
        </div>
        {lead.phone && <p>{dialerPhoneConnectionId
          ? "To call: copy the number or open the selected phone, then dial it yourself. Corral won’t start a call."
          : "To call: copy the number and dial it using your approved phone. Select an embedded phone to open it here."}</p>}
      </div>
      <button
        className="primary-button lead-agent-email-shortcut"
        type="button"
        disabled={!canUseAgent || !canEdit || !lead.email || busy}
        onClick={startEmailDraft}
        title={!lead.email ? "Add an email address to this lead first." : !canEdit ? "Lead-edit access is required to create a follow-up." : undefined}
      >
        Write follow-up email
      </button>
      <small className="lead-agent-email-shortcut-note">Draft, edit, and send it here after review.</small>
      {quickActionNotice && <p className="lead-agent-quick-notice" role="status">{quickActionNotice}</p>}
    </section>
    <div className="lead-agent-transcript" ref={transcriptRef} aria-live="polite">
      {!messages.length && <div className="lead-agent-welcome">
        <h3>What would you like to do with this lead?</h3>
        <p>{canUseAgent ? "I can answer from the record, update details, save notes and progress, prepare email drafts, or open the right lead workflow. You’ll confirm email sends, CRM syncs, and deletion." : "Your workspace role does not include AI action access. Ask a workspace admin to grant “Execute permitted agent actions”."}</p>
        {canUseAgent && <div className="lead-agent-suggestions">{suggestions.map((item) => <button type="button" key={item} onClick={() => void submit(undefined, item)}>{item}</button>)}</div>}
      </div>}
      {messages.map((message) => <article className={`lead-agent-message is-${message.role}`} key={message.id}>
        <span className="lead-agent-role">{message.role === "user" ? "You" : "Corral"}</span>
        <p>{message.content}</p>
        {message.action && renderAction(message)}
      </article>)}
      {busy && <div className="lead-agent-thinking" role="status"><i /><i /><i /><span>Checking this lead’s record…</span></div>}
      {error && <p className="lead-agent-error" role="alert">{error}</p>}
    </div>
    <form className="lead-agent-composer" onSubmit={(event) => void submit(event)}>
      <label htmlFor="lead-agent-input">Message Corral</label>
      <div><textarea
        id="lead-agent-input"
        rows={3}
        maxLength={3000}
        placeholder="Ask about this lead or describe an update…"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            void submit();
          }
        }}
      /><button className="primary-button" type="submit" disabled={!canUseAgent || busy || !draft.trim()}>{busy ? "Thinking…" : "Send"}</button></div>
      <small>Corral uses this lead’s record and recent history. Review proposed changes before applying them.</small>
    </form>
  </section>;
}
