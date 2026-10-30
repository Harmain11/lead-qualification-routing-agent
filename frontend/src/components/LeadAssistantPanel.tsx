import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  chatWithLeadAssistant,
  completeLeadManualReview,
  deleteLead,
  getLead,
  getLeadClosingOptions,
  listIntegrations,
  reevaluateLead,
  saveLeadNote,
  sendLeadToCrm,
  syncLeadGmailConversation,
  updateLeadProfile,
  updateLeadProgress,
  type LeadAssistantAction,
  type LeadAssistantReply,
  type WorkspaceAssistantLeadLink,
} from "../api";
import type { Lead, ProviderConnection } from "../types";
import type { LeadDetailSection } from "../leadSections";
import { OPEN_DIALER_PHONE_EVENT } from "./dialerPhoneConfig";
import { EmailComposer, type GmailComposerSeed } from "./EmailComposer";
import { createClientId } from "../clientIds";
import { assistantConversation } from "../assistantConversation";
import { isFollowUpEmailDraftRequest } from "./leadAssistantIntents";
import { AssistantNoteDraft } from "./AssistantNoteDraft";
import { AgentLeadChoices } from "./AgentLeadChoices";
import "../views/emailWorkspace.css";
import "./leadAssistant.css";
import { confirmAction } from "../v3/ui/confirm";

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  action?: LeadAssistantAction;
  leadLinks?: WorkspaceAssistantLeadLink[];
  busy?: boolean;
  done?: boolean;
  error?: string;
  notice?: string;
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
  canManageManualReview = false,
  canCrmSync,
  canViewEmail,
  canUseAgent,
  dialerPhoneConnectionId,
  onNavigateToDialer,
  onOpenLead,
  onUpdated,
  onDeleted,
  onSectionChange,
}: {
  lead: Lead;
  canEdit: boolean;
  canManageManualReview?: boolean;
  canCrmSync: boolean;
  canViewEmail: boolean;
  canUseAgent: boolean;
  dialerPhoneConnectionId: string | null;
  onNavigateToDialer: () => void;
  onOpenLead: (leadId: string) => void;
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
  const [gmailAccounts, setGmailAccounts] = useState<ProviderConnection[]>([]);
  const [composeSeed, setComposeSeed] = useState<GmailComposerSeed | null>(null);
  const [composerMessageId, setComposerMessageId] = useState<string | null>(null);
  const [composerRevision, setComposerRevision] = useState(0);
  const [selectedCrm, setSelectedCrm] = useState(lead.crm_sync?.provider || "");
  const [closingSelection, setClosingSelection] = useState<Record<string, ClosingSelection>>({});
  const transcriptRef = useRef<HTMLDivElement>(null);
  const actionInFlightRef = useRef(new Set<string>());
  const leadClassification = lead.qualification?.classification.toLowerCase();
  const followUpBlockedReason =
    Boolean(lead.qualification?.hard_disqualified) ||
    ["disqualify", "disqualified", "hard_disqualified"].includes(leadClassification || "")
      ? "This lead is disqualified. Review its qualification before sending outreach."
      : lead.routing?.manual_review || lead.analysis?.requires_manual_review
        ? "This lead needs human review before sending outreach."
        : null;

  useEffect(() => {
    setMessages([]);
    setDraft("");
    setError("");
    setQuickActionNotice("");
    setComposeSeed(null);
    setComposerMessageId(null);
  }, [lead.id]);

  useEffect(() => {
    transcriptRef.current?.scrollTo({ top: transcriptRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy, composeSeed]);

  useEffect(() => {
    if (!canEdit) return;
    getLeadClosingOptions().then(setClosingOptions).catch(() => setClosingOptions([]));
  }, [canEdit, lead.id]);

  useEffect(() => {
    if (!canCrmSync && !(canUseAgent && canEdit)) return;
    listIntegrations().then((cards) => {
      if (canUseAgent && canEdit) {
        const gmail = cards.find((card) => card.provider === "gmail");
        setGmailAccounts(gmail?.connections.length ? gmail.connections : gmail?.connection ? [gmail.connection] : []);
      }
      if (!canCrmSync) return;
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
    }).catch(() => {
      if (canUseAgent && canEdit) setGmailAccounts([]);
      if (canCrmSync) setCrmOptions([]);
    });
  }, [canCrmSync, canEdit, canUseAgent, lead.id]);

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
    const emailDraftRequested = isFollowUpEmailDraftRequest(content);
    try {
      const reply: LeadAssistantReply = await chatWithLeadAssistant(
        lead.id,
        assistantConversation(history),
      );
      const isDialerAction = reply.action.kind === "open_dialer";
      const isEmailComposerAction =
        reply.action.kind === "draft_email" || reply.action.kind === "send_follow_up";
      const assistantMessageId = id();
      const aiComposerOpened = isEmailComposerAction && canEdit && canViewEmail && openFollowUpComposer({
        subject: reply.action.email_subject?.trim() || undefined,
        body: reply.action.email_body?.trim() || undefined,
      }, assistantMessageId);
      const manualComposerFallbackOpened =
        !aiComposerOpened && emailDraftRequested && canEdit && canViewEmail && openFollowUpComposer({}, assistantMessageId);
      const emailComposerOpened = aiComposerOpened || manualComposerFallbackOpened;
      setMessages((current) => [...current, {
        id: assistantMessageId,
        role: "assistant",
        content: emailComposerOpened
          ? aiComposerOpened
            ? "I’ve opened an editable follow-up email below. Review and edit it before sending. Nothing is sent, and a meeting is not confirmed unless the calendar reports a successful booking."
            : "I couldn’t generate a draft from that response, so I opened the full email editor below. Add or edit the recipient, subject, and message there; nothing is sent automatically."
          : isDialerAction
          ? `${reply.reply}\n\nClick below to open the Dialer. Corral will not place the call.`
          : reply.reply,
        action: reply.action,
        leadLinks: reply.lead_links,
        ...(aiComposerOpened ? {
          done: true,
          actionResult: "The composer is open. Review all fields; nothing will be sent until you choose Send follow-up.",
        } : {}),
      }]);
    } catch (reason) {
      const assistantMessageId = id();
      if (emailDraftRequested && canEdit && canViewEmail && openFollowUpComposer({}, assistantMessageId)) {
        setMessages((current) => [...current, {
          id: assistantMessageId,
          role: "assistant",
          content: "AI drafting isn’t available right now, so I opened the full email editor for you to write the follow-up manually. Nothing is sent automatically.",
        }]);
      } else {
        setError(reason instanceof Error ? reason.message : "The assistant could not answer. Try again.");
      }
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

  function openFollowUpComposer(
    seed: GmailComposerSeed = {},
    messageId: string,
  ) {
    setQuickActionNotice("");
    setComposeSeed({
      ...seed,
      recipient: seed.recipient ?? lead.email?.trim() ?? "",
      purpose: "follow_up",
    });
    setComposerMessageId(messageId);
    setComposerRevision((current) => current + 1);
    return true;
  }

  function closeFollowUpComposer() {
    setComposeSeed(null);
    setComposerMessageId(null);
  }

  function startEmailDraft() {
    if (!canUseAgent || !canEdit || !canViewEmail || busy) return;
    void submit(undefined, "Write and send a follow-up email");
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
    if (action.kind === "complete_manual_review" && !canManageManualReview) {
      changeMessage(message.id, { error: "You need manual-review management access to complete this review." });
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
    if (["draft_email", "send_follow_up"].includes(action.kind) && !canViewEmail) {
      changeMessage(message.id, { error: "You do not have access to this lead’s email section." });
      return;
    }
    actionInFlightRef.current.add(message.id);
    changeMessage(message.id, { busy: true, error: undefined });
    try {
      if (action.kind === "reevaluate_lead") {
        if (!(await confirmAction({
          title: `Re-evaluate ${lead.full_name}?`,
          message: "Qualification and routing are recalculated from the current profile and recorded history. The owner may change.",
          confirmLabel: "Re-evaluate",
        }))) return;
        const updated = await reevaluateLead(lead.id);
        onUpdated(updated);
        changeMessage(message.id, { done: true });
      } else if (action.kind === "complete_manual_review") {
        const updated = await completeLeadManualReview(lead.id);
        onUpdated(updated);
        changeMessage(message.id, {
          done: true,
          actionResult: "Manual review completed. The hold was cleared and current routing applied; the assignee may be notified.",
        });
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
        await saveLeadNote(lead.id, action.note.trim());
        changeMessage(message.id, {
          done: true,
          actionResult: "Note saved to Team notes. Sales status was not changed.",
        });
        try {
          onUpdated(await reevaluateLead(lead.id));
          changeMessage(message.id, {
            done: true,
            actionResult: "Note saved and included in the updated AI assessment.",
          });
        } catch {
          changeMessage(message.id, {
            done: true,
            actionResult: "Note saved, but the AI assessment could not be refreshed. Try re-evaluating again later.",
          });
          void refreshLeadAfterAction(message.id);
        }
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
        const result = await updateLeadProgress(lead.id, {
          stage: action.progress_stage,
          note: action.progress_note,
          ...(converted ? {
            conversion_type: conversionType as "project" | "sale",
            credited_user_id: creditedUserId,
            ...(revenue !== undefined ? { closed_revenue: revenue } : {}),
          } : {}),
        });
        changeMessage(message.id, {
          done: true,
          actionResult: `Progress recorded and qualification re-evaluated as ${result.classification} (${result.score}/100).`,
        });
        void refreshLeadAfterAction(message.id);
      } else if (action.kind === "draft_email") {
        if (!openFollowUpComposer({
          subject: action.email_subject?.trim() || undefined,
          body: action.email_body?.trim() || undefined,
        }, message.id)) throw new Error(followUpBlockedReason || "The follow-up composer could not be opened.");
        changeMessage(message.id, {
          done: true,
          actionResult: "The follow-up composer is open. Check the sender, recipient, and message, then choose Send follow-up.",
        });
      } else if (action.kind === "send_follow_up") {
        if (!openFollowUpComposer({}, message.id)) throw new Error(followUpBlockedReason || "The follow-up composer could not be opened.");
        changeMessage(message.id, {
          done: true,
          actionResult: "The follow-up composer is open. Check the sender, recipient, and message, then choose Send follow-up.",
        });
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
        if (!(await confirmAction({ title: `Sync ${lead.full_name} to ${crmName(provider)}?`, message: "The CRM record is created or updated with this lead's current details.", confirmLabel: "Sync now" }))) return;
        changeMessage(message.id, { busy: true, error: undefined });
        await sendLeadToCrm(lead.id, provider);
        changeMessage(message.id, { busy: false, done: true });
        void refreshLeadAfterAction(message.id);
      } else if (action.kind === "delete_lead") {
        if (!canEdit) throw new Error("You need lead-edit access to delete this lead.");
        if (!(await confirmAction({ title: `Delete ${lead.full_name}?`, message: "The lead is removed from this workspace. A later matching inquiry may be imported again.", confirmLabel: "Delete lead", tone: "danger" }))) return;
        await deleteLead(lead.id);
        onDeleted(lead.id);
      } else if (action.kind === "open_email") {
        onSectionChange("email");
        changeMessage(message.id, { done: true });
      } else if (action.kind === "open_meetings") {
        onSectionChange("pipeline");
        changeMessage(message.id, {
          done: true,
          actionResult: "Meeting controls opened in Pipeline. Review the booked event, enter a reason to cancel or select a new time. Corral has not changed the calendar.",
        });
      } else if (action.kind === "open_dialer") {
        if (dialerPhoneConnectionId && lead.phone?.trim()) {
          window.dispatchEvent(new CustomEvent(OPEN_DIALER_PHONE_EVENT, {
            detail: { connectionId: dialerPhoneConnectionId },
          }));
        }
        changeMessage(message.id, {
          done: true,
          actionResult: lead.phone
            ? `Dialer opened. Dial ${lead.phone} yourself. Corral has not placed a call.`
            : "Dialer opened. Corral has not placed a call.",
        });
        onNavigateToDialer();
      } else if (action.kind === "open_closing") {
        onSectionChange("deal");
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
      case "add_note": return "Draft note / comment";
      case "record_progress": return "Record lead progress";
      case "draft_email": return "Write follow-up email";
      case "send_follow_up": return "Compose follow-up email";
      case "refresh_email": return "Refresh Gmail conversation";
      case "sync_crm": return "Sync to CRM";
      case "delete_lead": return "Delete this lead";
      case "reevaluate_lead": return "Re-evaluate qualification and routing";
      case "complete_manual_review": return "Complete manual review";
      case "open_email": return "Open email";
      case "open_meetings": return "Open meeting controls";
      case "open_dialer": return "Open Dialer to call";
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
    return <details className="lead-agent-action" open={!message.done}>
      <summary className="lead-agent-action-title"><span aria-hidden="true">↳</span><strong>{actionTitle(action)}</strong>{message.done && <small>{["draft_email", "send_follow_up"].includes(action.kind) ? "Composer opened" : action.kind === "open_dialer" ? "Dialer opened · not called" : action.kind.startsWith("open_") ? "Opened" : "Completed"}</small>}</summary>
      {action.kind === "update_profile" && <dl className="lead-agent-changes">
        {changes.map(([field, value]) => <div key={field}><dt>{fieldLabels[field] || field}</dt><dd>{String(value)}</dd></div>)}
        {action.clear_fields.map((field) => <div key={`clear-${field}`}><dt>{fieldLabels[field] || field}</dt><dd>Clear this field</dd></div>)}
      </dl>}
      {action.kind === "add_note" && (message.done
        ? <p className="lead-agent-action-copy">{action.note}</p>
        : <AssistantNoteDraft
            value={action.note ?? ""}
            disabled={message.busy || !canEdit}
            onChange={(note) => changeMessage(message.id, {
              action: { ...action, note }, error: undefined,
            })}
          />)}
      {action.kind === "open_dialer" && <p className="lead-agent-action-copy">Saved number: <b>{lead.phone || "Not available"}</b>. Corral will not place the call. {!message.done && <a className="lead-agent-inline-link" href="?view=dialer" onClick={(event) => { event.preventDefault(); void executeAction(message); }}>Click here to open the Dialer</a>}</p>}
      {action.kind === "complete_manual_review" && <p className="lead-agent-action-copy">This clears the hold, applies current routing rules, and may notify the assignee.</p>}
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
      {message.actionResult && <p className="lead-agent-notice" role="status">{message.actionResult}</p>}
      {message.error && <p className="lead-agent-error" role="alert">{message.error}</p>}
      {message.notice && <p className="lead-agent-notice" role="status">{message.notice}</p>}
      {!message.done && action.kind !== "open_dialer" && <div className="lead-agent-action-buttons">
        <button
            className={["sync_crm", "delete_lead", "send_follow_up", "reevaluate_lead", "complete_manual_review"].includes(action.kind) ? "secondary-button" : "primary-button"}
            type="button"
            disabled={message.busy || (action.kind === "add_note" && !action.note?.trim()) || (editRequired && !canEdit) || (action.kind === "complete_manual_review" && !canManageManualReview) || (action.kind === "sync_crm" && (!canCrmSync || !selectedCrm)) || (action.kind === "open_email" && !canViewEmail) || (needsCredit && (!selection.conversionType || !selection.creditedUserId))}
            onClick={() => void executeAction(message)}
          >
            {message.busy ? "Working…" : editRequired && !canEdit ? "Lead-edit access required" : action.kind === "complete_manual_review" && !canManageManualReview ? "Manual-review access required" : action.kind === "sync_crm" && !canCrmSync ? "CRM-sync access required" : action.kind === "sync_crm" ? "Confirm CRM sync" : action.kind === "delete_lead" ? "Confirm delete" : action.kind === "reevaluate_lead" ? "Confirm re-evaluation" : action.kind === "complete_manual_review" ? "Confirm review complete" : action.kind === "send_follow_up" || action.kind === "draft_email" ? "Open email composer" : action.kind === "refresh_email" ? "Refresh Gmail" : action.kind === "record_progress" ? "Save progress" : action.kind === "update_profile" ? "Apply changes" : action.kind === "add_note" ? "Save note" : action.kind === "open_meetings" ? "Review available times" : action.kind === "open_email" ? (canViewEmail ? "Open email section" : "Email access required") : action.kind === "open_closing" ? "Open closing section" : action.kind === "open_history" ? "Open history" : "Continue"}
          </button>
          {action.kind === "add_note" && <button
            className="secondary-button"
            type="button"
            disabled={message.busy}
            onClick={() => changeMessage(message.id, {
              done: true, action: undefined, error: undefined,
              actionResult: "Draft discarded. No note was saved.",
              content: "Draft discarded. No note was saved.",
            })}
          >Discard draft</button>}
      </div>}
    </details>;
  }

  return <section className="lead-agent" aria-label={`Assistant for ${lead.full_name}`}>
    <header className="lead-agent-heading">
      <div className="lead-agent-mark" aria-hidden="true">CA</div>
      <div><span className="eyebrow">Lead assistant</span><h2>Work with {lead.full_name}</h2><p>Ask Corral to help with this lead. Check any proposed changes, then choose the action you want.</p></div>
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
        disabled={!canUseAgent || !canEdit || !canViewEmail || busy}
        onClick={startEmailDraft}
        title={followUpBlockedReason || (!canEdit ? "Lead-edit access is required to send a follow-up." : !canViewEmail ? "Email access is required to compose a follow-up." : undefined)}
      >
        Compose follow-up email
      </button>
      <small className="lead-agent-email-shortcut-note">Choose sender and recipient, format the message, then send it directly.</small>
      {(quickActionNotice || followUpBlockedReason) && <p className="lead-agent-quick-notice" role="status">{quickActionNotice || followUpBlockedReason}</p>}
    </section>
    <div className="lead-agent-transcript" ref={transcriptRef} aria-live="polite">
      {!messages.length && <div className="lead-agent-welcome">
        <h3>What would you like to do with this lead?</h3>
        <p>{canUseAgent ? "I can answer from the record, update details, save notes and progress, compose emails, or open the right lead workflow. You’ll confirm email sends, CRM syncs, and deletion." : "Your workspace role does not include AI action access. Ask a workspace admin to grant “Execute permitted agent actions”."}</p>
        {canUseAgent && <div className="lead-agent-suggestions">{suggestions.map((item) => <button type="button" key={item} onClick={() => void submit(undefined, item)}>{item}</button>)}</div>}
      </div>}
      {messages.map((message) => <article className={`lead-agent-message is-${message.role}`} key={message.id}>
        <span className="lead-agent-role">{message.role === "user" ? "You" : "Corral"}</span>
        <p>{message.content}</p>
        {message.role === "assistant" && Boolean(message.leadLinks?.length) && (
          <AgentLeadChoices
            leads={message.leadLinks!}
            onSelect={onOpenLead}
          />
        )}
        {message.action && renderAction(message)}
        {composeSeed && composerMessageId === message.id && <div className="lead-agent-email-compose">
          <EmailComposer
            key={composerRevision}
            accounts={gmailAccounts}
            canSend={canEdit}
            sendBlockedReason={followUpBlockedReason ?? undefined}
            leadId={lead.id}
            seed={composeSeed}
            onClose={closeFollowUpComposer}
            onSent={() => {
              changeMessage(message.id, { actionResult: "Follow-up email sent." });
              closeFollowUpComposer();
              setQuickActionNotice("Follow-up email sent.");
              void getLead(lead.id).then(onUpdated).catch(() => {
                setQuickActionNotice("Follow-up email sent. The lead details could not be refreshed.");
              });
            }}
          />
        </div>}
      </article>)}
      {busy && <div className="lead-agent-thinking" role="status"><i /><i /><i /><span>Checking this lead’s record…</span></div>}
      {error && <p className="lead-agent-error" role="alert">{error}</p>}
    </div>
    <form className="lead-agent-composer" onSubmit={(event) => void submit(event)}>
      <label htmlFor="lead-agent-input">Message Corral</label>
      <div><textarea
        id="lead-agent-input"
        rows={4}
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
