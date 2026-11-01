import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  chatWithWorkspaceAssistant,
  listGmailSendAccounts,
  getLead,
  listWorkspaceAgentConversations,
  createWorkspaceAgentConversation,
  getWorkspaceAgentConversation,
  saveWorkspaceAgentConversation,
  deleteWorkspaceAgentConversation,
  type WorkspaceAgentConversationMessage,
  type WorkspaceAgentConversationSummary,
  sendGmailEmail,
  type WorkspaceAssistantAction,
  type WorkspaceAssistantArea,
  type WorkspaceAssistantLeadLink,
} from "../api";
import type { GmailSendAccount, Lead } from "../types";
import { createClientId } from "../clientIds";
import { assistantConversation } from "../assistantConversation";
import { AssistantNoteDraft } from "./AssistantNoteDraft";
import { AssistantLeadDraft } from "./AssistantLeadDraft";
import { AgentLeadChoices } from "./AgentLeadChoices";
import { AssistantResultActions } from "./AssistantResultActions";
import { isDirectPhoneCallRequest } from "./workspaceAssistantIntents";
import { parseAssistantCommand } from "./assistantCommands";
import { TOGGLE_AGENT_EVENT } from "./shell/agentEvents";
import {
  ASSISTANT_AREAS,
  automationDraftSpec,
  applyWorkspaceAssistantAction,
  applyWorkspaceEmailSignature,
  canOpenAssistantArea,
  canApplyWorkspaceAssistantAction,
  workspaceNoteDraft,
  editWorkspaceNoteDraft,
  leadCreationDraft,
  createWorkspaceLead,
} from "./workspaceAssistantActions";
import "../views/emailWorkspace.css";

type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  action?: WorkspaceAssistantAction;
  actionStatus?: "applying" | "sending" | "applied" | "dismissed";
  actionResult?: string;
  actionError?: string;
  contextWarnings?: string[];
  leadLinks?: WorkspaceAssistantLeadLink[];
  needsVerification?: boolean;
};

const prompts: Array<{
  label: string;
  prompt: string;
  requires?: string[];
}> = [
  {
    label: "Start a quote or proposal",
    prompt: "I need a quote or proposal. If no lead is verified yet, ask for its exact name or email. Then open that lead's existing Deal Desk. Do not create, approve, or send anything.",
    requires: ["leads.view"],
  },
  {
    label: "Use a sequence or playbook",
    prompt: "Explain how to enroll a lead in an existing sequence or playbook, personalize steps, pause or resume a run, detect replies, and review approval-gated outgoing drafts. Do not enroll anyone or send anything.",
    requires: ["agents.view"],
  },
  {
    label: "Coach the pipeline from recorded data",
    prompt: "Coach the pipeline using only recorded analytics and verified workspace records. Flag stalled or at-risk deals only when actual data supports it, recommend next actions, and do not invent forecasts or probabilities.",
    requires: ["analytics.view"],
  },
  {
    label: "Coach me on a recent call",
    prompt: "Coach me on my most recent recorded call with a lead. Give a short summary, what went well, what to improve, a talk track for the next touch and one next step with a date. Use only the recorded transcript and notes; if none exist, say so.",
    requires: ["leads.view"],
  },
  {
    label: "Write meeting minutes",
    prompt: "Write minutes for a recent meeting with a lead: attendees, summary, decisions, action items with owners, and the next step. Ask me which lead if it is unclear. Use only recorded notes and transcripts.",
    requires: ["leads.view"],
  },
  {
    label: "Check workspace document sources",
    prompt: "Tell me whether a connected workspace document source is available and what you can cite. If none is connected, say so; do not guess about document content.",
  },
  {
    label: "Explain approvals and audit history",
    prompt: "Explain where proposals stay reviewable, where completed actions are auditable, which data or provider source is actually recorded, and whether a specific action supports reversal. Do not claim universal undo.",
  },
  {
    label: "Find a lead and summarize recent interactions",
    prompt: "Find a lead and summarize recent emails, calls, or meetings.",
    requires: ["leads.view"],
  },
  {
    label: "Draft an email for review",
    prompt: "Draft an email for me to review before sending.",
    requires: ["leads.edit"],
  },
  {
    label: "Update a lead after review",
    prompt: "Update a lead’s details or add a note after review.",
    requires: ["leads.view", "leads.edit"],
  },
];

const STEP_LABELS: Record<string, string> = {
  wait: "Wait", draft: "Draft email", research: "Research", coach: "Coaching tip", note: "Note",
};

function AutomationDraftPreview({ action }: { action: WorkspaceAssistantAction }) {
  let spec: ReturnType<typeof automationDraftSpec> | null = null;
  try {
    spec = automationDraftSpec(JSON.parse(action.payload_json ?? "{}"));
  } catch {
    return <p className="corral-workspace-error" role="alert">This draft is incomplete. Ask Corral to rebuild it.</p>;
  }
  const steps = spec.nodes.slice(1, -1);
  return (
    <ol className="corral-automation-draft" aria-label={`${spec.name} steps`}>
      {steps.map((step, index) => (
        <li key={step.id}>
          <span className="corral-automation-draft-index">{index + 1}</span>
          <div>
            <strong>
              {STEP_LABELS[step.type] ?? step.type}
              {step.type === "wait" ? ` ${step.wait_hours >= 24 && step.wait_hours % 24 === 0 ? `${step.wait_hours / 24} day${step.wait_hours === 24 ? "" : "s"}` : `${step.wait_hours} h`}` : ""}
            </strong>
            {step.text && <p>{step.text}</p>}
          </div>
        </li>
      ))}
    </ol>
  );
}

type Props = {
  permissions: ReadonlySet<string>;
  onNavigate: (area: WorkspaceAssistantArea) => void;
  onInspectLead: (leadId: string, section?: "deal") => void;
  onRefresh?: () => void | Promise<void>;
  onLeadUpdated?: (lead: Lead) => void;
};

export function WorkspaceAssistantChat({
  permissions,
  onNavigate,
  onInspectLead,
  onRefresh,
  onLeadUpdated,
}: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const conversationIdRef = useRef<string | null>(null);
  const [conversations, setConversations] = useState<WorkspaceAgentConversationSummary[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState("");
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [historyChanging, setHistoryChanging] = useState(false);
  const historyChangingRef = useRef(false);
  const saveQueueRef = useRef<Promise<unknown>>(Promise.resolve());
  const autosaveTimerRef = useRef<number | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [sendAccounts, setSendAccounts] = useState<GmailSendAccount[]>([]);
  const [sendAccountsLoading, setSendAccountsLoading] = useState(false);
  const [sendAccountsError, setSendAccountsError] = useState("");
  const [selectedSendAccountId, setSelectedSendAccountId] = useState("");
  const transcriptRef = useRef<HTMLDivElement>(null);
  const emailEditorsRef = useRef(new Map<string, HTMLDivElement>());
  const sendingMessageIdsRef = useRef(new Set<string>());
  const applyingMessageIdsRef = useRef(new Set<string>());
  const canSendEmail = permissions.has("leads.edit");
  const threadBusy = busy || historyLoading || historyChanging
    || messages.some((message) => message.actionStatus === "applying" || message.actionStatus === "sending");

  function saveTranscript(
    id: string,
    transcript: Message[],
  ): Promise<{ id: string; title: string; message_count: number }> {
    const savedMessages: WorkspaceAgentConversationMessage[] = transcript.map((message) => ({
      id: message.id,
      role: message.role,
      content: message.content,
      ...(message.action ? { action: message.action } : {}),
      ...(message.actionStatus ? { actionStatus: message.actionStatus } : {}),
      ...(message.actionResult ? { actionResult: message.actionResult } : {}),
      ...(message.actionError ? { actionError: message.actionError } : {}),
      ...(message.contextWarnings ? { contextWarnings: message.contextWarnings } : {}),
      ...(message.leadLinks ? { leadLinks: message.leadLinks } : {}),
      ...(message.needsVerification ? { needsVerification: true } : {}),
    }));
    const next = saveQueueRef.current.catch(() => undefined).then(() =>
      saveWorkspaceAgentConversation(id, savedMessages),
    );
    saveQueueRef.current = next;
    return next;
  }

  async function flushActiveConversation() {
    if (autosaveTimerRef.current !== null) {
      window.clearTimeout(autosaveTimerRef.current);
      autosaveTimerRef.current = null;
    }
    const activeId = conversationIdRef.current;
    if (activeId && messages.length) await saveTranscript(activeId, messages);
    await saveQueueRef.current.catch(() => undefined);
  }

  useEffect(() => {
    let active = true;
    void listWorkspaceAgentConversations()
      .then(async ({ items }) => {
        if (!active) return;
        setConversations(items);
        if (!items.length) return;
        const saved = await getWorkspaceAgentConversation(items[0].id);
        if (!active) return;
        const restored = saved.messages.map((message): Message => ({
          ...message,
          ...(message.actionStatus === "applying" || message.actionStatus === "sending"
            ? {
                actionStatus: undefined,
                needsVerification: true,
                actionError: "This action was in progress when the chat closed. Check whether it completed before trying again.",
              }
            : {}),
        }));
        conversationIdRef.current = saved.id;
        setConversationId(saved.id);
        setMessages(restored);
      })
      .catch((reason) => {
        if (active) setHistoryError(reason instanceof Error ? reason.message : "Chat history could not be loaded.");
      })
      .finally(() => {
        if (active) setHistoryLoading(false);
      });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!conversationId || !messages.length) return;
    const timer = window.setTimeout(() => {
      autosaveTimerRef.current = null;
      void saveTranscript(conversationId, messages)
        .then((saved) => {
          setConversations((current) => [
            {
              id: saved.id,
              title: saved.title,
              message_count: saved.message_count,
              preview: [...messages].reverse().find((message) => message.role === "user")?.content ?? "",
              updated_at: new Date().toISOString(),
            },
            ...current.filter((item) => item.id !== saved.id),
          ].slice(0, 100));
          setHistoryError("");
        })
        .catch(() => setHistoryError("This chat could not be saved. Your messages are still visible here."));
    }, 400);
    autosaveTimerRef.current = timer;
    return () => {
      window.clearTimeout(timer);
      if (autosaveTimerRef.current === timer) autosaveTimerRef.current = null;
    };
  }, [conversationId, messages]);

  async function openConversation(id: string) {
    if (threadBusy || historyChangingRef.current) return;
    if (id === conversationIdRef.current) {
      setHistoryOpen(false);
      return;
    }
    historyChangingRef.current = true;
    setHistoryChanging(true);
    try {
      await flushActiveConversation();
      const saved = await getWorkspaceAgentConversation(id);
      const restored = saved.messages.map((message): Message => ({
        ...message,
        ...(message.actionStatus === "applying" || message.actionStatus === "sending"
          ? {
              actionStatus: undefined,
              needsVerification: true,
              actionError: "This action was in progress when the chat closed. Check whether it completed before trying again.",
            }
          : {}),
      }));
      conversationIdRef.current = saved.id;
      setConversationId(saved.id);
      setMessages(restored);
      setDraft("");
      setError("");
      setConfirmDeleteId(null);
      setHistoryOpen(false);
      setHistoryError("");
    } catch (reason) {
      setHistoryError(reason instanceof Error ? reason.message : "That conversation could not be opened.");
    } finally {
      historyChangingRef.current = false;
      setHistoryChanging(false);
    }
  }

  async function startNewConversation() {
    if (threadBusy || historyChangingRef.current) return;
    historyChangingRef.current = true;
    setHistoryChanging(true);
    try {
      await flushActiveConversation();
      conversationIdRef.current = null;
      setConversationId(null);
      setMessages([]);
      setDraft("");
      setConfirmDeleteId(null);
      setHistoryOpen(false);
      setError("");
      setHistoryError("");
    } catch {
      setHistoryError("The previous chat could not be saved.");
    } finally {
      historyChangingRef.current = false;
      setHistoryChanging(false);
    }
  }

  async function removeConversation(id: string) {
    if (threadBusy || historyChangingRef.current) return;
    historyChangingRef.current = true;
    setHistoryChanging(true);
    try {
      await flushActiveConversation();
      await deleteWorkspaceAgentConversation(id);
      setConfirmDeleteId(null);
      setConversations((current) => current.filter((item) => item.id !== id));
      if (id === conversationIdRef.current) {
        conversationIdRef.current = null;
        setConversationId(null);
        setMessages([]);
        setDraft("");
        setError("");
      }
    } catch (reason) {
      setHistoryError(reason instanceof Error ? reason.message : "That conversation could not be deleted.");
    } finally {
      historyChangingRef.current = false;
      setHistoryChanging(false);
    }
  }

  useEffect(() => {
    if (!canSendEmail) {
      setSendAccounts([]);
      setSelectedSendAccountId("");
      setSendAccountsError("");
      setSendAccountsLoading(false);
      return;
    }
    let active = true;
    setSendAccountsLoading(true);
    setSendAccountsError("");
    void listGmailSendAccounts()
      .then((accounts) => {
        if (!active) return;
        setSendAccounts(accounts);
        setSelectedSendAccountId((current) =>
          accounts.some((account) => account.id === current)
            ? current
            : accounts[0]?.id ?? "",
        );
      })
      .catch((reason) => {
        if (!active) return;
        setSendAccounts([]);
        setSendAccountsError(
          reason instanceof Error
            ? reason.message
            : "Gmail send access could not be checked.",
        );
      })
      .finally(() => {
        if (active) setSendAccountsLoading(false);
      });
    return () => {
      active = false;
    };
  }, [canSendEmail]);

  useEffect(() => {
    transcriptRef.current?.scrollTo({
      top: transcriptRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages, busy]);

  async function submit(event?: FormEvent, text = draft) {
    event?.preventDefault();
    const content = text.trim();
    if (!content || threadBusy || historyChangingRef.current
      || applyingMessageIdsRef.current.size || sendingMessageIdsRef.current.size) return;
    if (messages.length >= 500) {
      setError("This chat reached its history limit. Start a new conversation to continue.");
      return;
    }

    // Safe UI moves run instantly; only work that needs judgement reaches the model.
    const command = parseAssistantCommand(content);
    if (command?.kind === "close") {
      setDraft("");
      window.dispatchEvent(new CustomEvent(TOGGLE_AGENT_EVENT, { detail: { open: false } }));
      return;
    }
    if (command?.kind === "new_chat") {
      void startNewConversation();
      return;
    }
    if (command?.kind === "navigate") {
      const { area } = command;
      const { label } = ASSISTANT_AREAS[area];
      const allowed = canOpenAssistantArea(area, permissions);
      if (allowed) onNavigate(area);
      setDraft("");
      setError("");
      setMessages((current) => [
        ...current,
        { id: createClientId(), role: "user", content },
        {
          id: createClientId(),
          role: "assistant",
          content: allowed
            ? `Opened ${label}.`
            : `Your workspace role can’t open ${label}. Ask a workspace admin for access.`,
        },
      ]);
      return;
    }

    const nextMessages = [
      ...messages,
      { id: createClientId(), role: "user" as const, content },
    ];
    setMessages(nextMessages);
    setDraft("");
    setBusy(true);
    setError("");
    const directCallRequested = isDirectPhoneCallRequest(content);
    const dialerAction: WorkspaceAssistantAction | undefined =
      directCallRequested && canOpenAssistantArea("dialer", permissions)
        ? {
            kind: "open_module",
            area: "dialer",
            title: "Open Dialer",
            summary: "Choose the lead or enter a number, then start the call yourself. Corral never places calls.",
            target_id: null,
            target_label: null,
            email: null,
            email_recipient: null,
            email_cc: null,
            email_bcc: null,
            email_subject: null,
            email_body: null,
            role: null,
            enabled: null,
            payload_json: null,
          }
        : undefined;
    const callInstruction = dialerAction
      ? "I can’t place or start phone calls for you. Open the Dialer below, choose the lead or enter their number, then start the call yourself."
      : "I can’t place or start phone calls for you, and your account doesn’t have Dialer access. Ask a workspace admin to grant it.";
    try {
      let activeConversationId = conversationIdRef.current;
      if (!activeConversationId) {
        const created = await createWorkspaceAgentConversation();
        activeConversationId = created.id;
        conversationIdRef.current = created.id;
        setConversationId(created.id);
      }
      // Save the user's turn before asking the model; this preserves context
      // if the provider request fails or the browser closes.
      await saveTranscript(activeConversationId, nextMessages);
      const result = await chatWithWorkspaceAssistant(
        assistantConversation(nextMessages),
        {},
        activeConversationId,
        nextMessages.length,
      );
      const action = directCallRequested
        ? dialerAction
        : result.action?.kind === "send_email"
        ? {
            ...result.action,
            email_body: applyWorkspaceEmailSignature(
              result.action.email_body ?? "",
              result.drafting_context ?? {},
            ),
          }
        : result.action;
      setMessages((current) => [
        ...current,
        {
          id: createClientId(),
          role: "assistant",
          content: directCallRequested ? callInstruction : result.reply,
          action: action?.kind !== "none" ? action : undefined,
          leadLinks: result.lead_links,
          contextWarnings: result.context_warnings,
        },
      ]);
      if (result.conversation_id && result.conversation_id !== activeConversationId) {
        setHistoryError("The reply was returned for a different chat. Start a new conversation and try again.");
      }
    } catch (reason) {
      if (directCallRequested) {
        setMessages((current) => [
          ...current,
          {
            id: createClientId(),
            role: "assistant",
            content: callInstruction,
            ...(dialerAction ? { action: dialerAction } : {}),
          },
        ]);
      } else {
        setError(
          reason instanceof Error
            ? reason.message
            : "Corral couldn’t answer. Try again.",
        );
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleAction(messageId: string, action: WorkspaceAssistantAction) {
    if (threadBusy || historyChangingRef.current
      || applyingMessageIdsRef.current.size || sendingMessageIdsRef.current.size
      || messages.find((message) => message.id === messageId)?.needsVerification) return;
    applyingMessageIdsRef.current.add(messageId);
    setMessages((current) =>
      current.map((message) =>
        message.id === messageId
          ? { ...message, actionStatus: "applying", actionError: undefined }
          : message,
      ),
    );
    try {
      const activeConversationId = conversationIdRef.current;
      if (activeConversationId) {
        const inProgress = messages.map((message) =>
          message.id === messageId ? { ...message, actionStatus: "applying" as const } : message,
        );
        await saveTranscript(activeConversationId, inProgress);
      }
      if (action.kind === "create_lead") {
        const draft = leadCreationDraft(action);
        const followUpNote = typeof draft.follow_up_note === "string"
          ? draft.follow_up_note.trim() : "";
        const noteMessageId = followUpNote ? createClientId() : null;
        const created = await createWorkspaceLead(action, permissions);
        // Mark creation complete before optional refreshes; a failed refresh
        // must never offer a retry that creates a duplicate lead.
        setMessages((current) => [
          ...current.map((message): Message => message.id === messageId ? {
            ...message,
            actionStatus: "applied",
            actionResult: `Created ${created.full_name || "the lead"} successfully.${followUpNote ? " The follow-up note below is still an unsaved draft." : ""}`,
          } : message),
          ...(noteMessageId ? [{
            id: noteMessageId,
            role: "assistant" as const,
            content: "The lead was created. Review or edit this follow-up note, then choose Save note. It has not been saved yet.",
            action: {
              ...action,
              kind: "add_lead_note" as const,
              target_id: created.id,
              target_label: created.full_name,
              title: "Draft follow-up note",
              summary: "Save this note only after reviewing it. Sales status will not change.",
              payload_json: JSON.stringify({ note: followUpNote }),
            },
          }] : []),
        ]);
        void Promise.resolve().then(() => onLeadUpdated?.(created)).catch(() => undefined);
        void Promise.resolve().then(() => onRefresh?.()).catch(() => undefined);
        return;
      }
      const result = await applyWorkspaceAssistantAction(
        action,
        permissions,
        onNavigate,
        onInspectLead,
      );
      setMessages((current) =>
        current.map((message) =>
          message.id === messageId
            ? { ...message, actionStatus: "applied", actionResult: result }
            : message,
        ),
      );
      // A refresh failure must not make a successful write appear to have failed.
      if (action.target_id && ["update_lead_profile", "add_lead_note", "record_lead_progress", "reevaluate_lead", "complete_lead_manual_review"].includes(action.kind) && onLeadUpdated) {
        void getLead(action.target_id).then(onLeadUpdated).catch(() => {
          setMessages((current) => current.map((message) => message.id === messageId ? {
            ...message, actionResult: `${result} Reopen the lead to refresh its displayed details.`,
          } : message));
        });
      } else {
        void Promise.resolve().then(() => onRefresh?.()).catch(() => undefined);
      }
    } catch (reason) {
      setMessages((current) =>
        current.map((message) =>
          message.id === messageId
            ? {
                ...message,
                actionStatus: undefined,
                actionError:
                  reason instanceof Error
                    ? reason.message
                    : "The proposed action could not be applied.",
              }
            : message,
        ),
      );
    } finally {
      applyingMessageIdsRef.current.delete(messageId);
    }
  }

  function updateEmailDraft(
    messageId: string,
    field:
      | "email_recipient"
      | "email_cc"
      | "email_bcc"
      | "email_subject"
      | "email_body"
      | "email_html_body",
    value: string,
  ) {
    setMessages((current) =>
      current.map((message) =>
        message.id === messageId && message.action
          ? { ...message, action: { ...message.action, [field]: value } }
          : message,
      ),
    );
  }

  function updateEmailBody(messageId: string, editor: HTMLDivElement) {
    const body = editor.innerText.replace(/\u00a0/g, " ");
    updateEmailDraft(messageId, "email_body", body);
    updateEmailDraft(messageId, "email_html_body", editor.innerHTML);
  }

  function formatEmailBody(
    messageId: string,
    command: "bold" | "italic" | "underline" | "insertUnorderedList" | "insertOrderedList",
  ) {
    const editor = emailEditorsRef.current.get(messageId);
    if (!editor) return;
    editor.focus();
    document.execCommand(command, false);
    updateEmailBody(messageId, editor);
  }

  async function handleSendEmail(
    messageId: string,
    action: WorkspaceAssistantAction,
  ) {
    const recipient = action.email_recipient?.trim() ?? "";
    const subject = action.email_subject?.trim() ?? "";
    const body = action.email_body ?? "";
    if (
      action.kind !== "send_email" ||
      threadBusy || historyChangingRef.current || applyingMessageIdsRef.current.size > 0 ||
      messages.find((message) => message.id === messageId)?.needsVerification ||
      !canApplyWorkspaceAssistantAction(action, permissions) ||
      !selectedSendAccountId ||
      !recipient ||
      !subject ||
      !body.trim() ||
      body.length > 8000 ||
      sendingMessageIdsRef.current.has(messageId)
    ) {
      return;
    }
    sendingMessageIdsRef.current.add(messageId);
    setMessages((current) =>
      current.map((message) =>
        message.id === messageId
          ? { ...message, actionStatus: "sending", actionError: undefined }
          : message,
      ),
    );
    try {
      const activeConversationId = conversationIdRef.current;
      if (activeConversationId) {
        const inProgress = messages.map((message) =>
          message.id === messageId ? { ...message, actionStatus: "sending" as const } : message,
        );
        await saveTranscript(activeConversationId, inProgress);
      }
      const result = await sendGmailEmail({
        connection_id: selectedSendAccountId,
        recipient,
        cc: action.email_cc?.trim() ?? "",
        bcc: action.email_bcc?.trim() ?? "",
        subject,
        body,
        ...(action.email_html_body?.trim()
          ? { html_body: action.email_html_body }
          : {}),
      });
      const account = sendAccounts.find(
        (item) => item.id === selectedSendAccountId,
      );
      setMessages((current) =>
        current.map((message) =>
          message.id === messageId
            ? {
                ...message,
                actionStatus: "applied",
                actionResult: `Email sent from ${account?.account_name ?? result.sender} to ${result.recipient}.`,
              }
            : message,
        ),
      );
    } catch (reason) {
      setMessages((current) =>
        current.map((message) =>
          message.id === messageId
            ? {
                ...message,
                actionStatus: undefined,
                actionError:
                  reason instanceof Error
                    ? reason.message
                    : "Email could not be sent.",
              }
            : message,
        ),
      );
    } finally {
      sendingMessageIdsRef.current.delete(messageId);
    }
  }

  function dismissAction(messageId: string) {
    setMessages((current) =>
      current.map((message) =>
        message.id === messageId
          ? { ...message, actionStatus: "dismissed" }
          : message,
      ),
    );
  }

  return (
    <section className="corral-workspace-chat" aria-label="Chat with Ask Corral">
      <header className="corral-workspace-chat-toolbar">
        <div className="corral-workspace-chat-toolbar__title">
          <strong>
            {conversations.find((conversation) => conversation.id === conversationId)?.title
              || "Ask Corral"}
          </strong>
          {historyError && <span role="status">{historyError}</span>}
        </div>
        <div className="corral-workspace-chat-toolbar__actions">
          <button type="button" className="secondary-button" onClick={() => void startNewConversation()} disabled={threadBusy}>
            New chat
          </button>
          <button
            type="button"
            className="secondary-button"
            aria-pressed={historyOpen}
            onClick={() => setHistoryOpen((open) => !open)}
            disabled={threadBusy}
          >
            {historyOpen ? "Back to chat" : "History"}
          </button>
        </div>
      </header>
      <div
        className="corral-workspace-transcript"
        ref={transcriptRef}
        aria-live="polite"
      >
        {historyOpen ? (
          <section className="corral-agent-history" aria-label="Saved conversations">
            <div className="corral-agent-history__heading">
              <div>
                <h2>Chat history</h2>
                <p>Your conversations are private to your account in this workspace.</p>
              </div>
            </div>
            {historyLoading ? (
              <p role="status">Loading chat history…</p>
            ) : conversations.length ? (
              <div className="corral-agent-history__list">
                {conversations.map((conversation) => (
                  <article className="corral-agent-history__item" key={conversation.id}>
                    <button
                      type="button"
                      className="corral-agent-history__open"
                      onClick={() => void openConversation(conversation.id)}
                      disabled={threadBusy}
                    >
                      <strong>{conversation.title}</strong>
                      <span>{conversation.preview}</span>
                      <small>{new Date(conversation.updated_at).toLocaleString()} · {conversation.message_count} messages</small>
                    </button>
                    <button
                      type="button"
                      className="corral-agent-history__delete"
                      aria-label={`Delete conversation: ${conversation.title}`}
                      onClick={() => setConfirmDeleteId((current) => current === conversation.id ? null : conversation.id)}
                      disabled={threadBusy}
                    >
                      {confirmDeleteId === conversation.id ? "Cancel" : "Delete"}
                    </button>
                    {confirmDeleteId === conversation.id && (
                      <div className="corral-agent-history__confirm">
                        <span>This permanently deletes the saved conversation.</span>
                        <button
                          type="button"
                          className="corral-agent-history__delete"
                          onClick={() => void removeConversation(conversation.id)}
                          disabled={threadBusy}
                        >
                          Delete chat
                        </button>
                      </div>
                    )}
                  </article>
                ))}
              </div>
            ) : (
              <p>No saved conversations yet. Start a chat and it will appear here.</p>
            )}
          </section>
        ) : (
        <>
        {!messages.length && (
          <div className="corral-workspace-welcome">
            <span className="corral-workspace-welcome-mark" aria-hidden="true">
              CA
            </span>
            <h2>Ask, plan, or create</h2>
            <p>
              Search permitted workspace records, get help with existing workflows,
              and prepare changes for review. Corral uses recorded data and will
              say when required information or a source is unavailable.
            </p>
            <div className="corral-workspace-status-grid">
              <section className="corral-workspace-status-card" aria-label="Workspace document source status">
                <div className="corral-workspace-status-heading">
                  <strong>Workspace documents</strong>
                </div>
                <p>
                  Corral can cite connected workspace documents only. None are
                  connected here, so it won’t guess what a playbook, pricing,
                  policy, or product document says.
                </p>
              </section>
              <section className="corral-workspace-status-card" aria-label="Approval and audit guidance">
                <div className="corral-workspace-status-heading">
                  <strong>Review and audit</strong>
                </div>
                <p>
                  Proposed changes stay in this saved conversation until you
                  approve or dismiss them. The conversation keeps the outcome;
                  AI Activity and record history show events logged by those
                  workflows. Reversal is available only when a specific action
                  supports it.
                </p>
              </section>
            </div>
            <div className="corral-workspace-prompts">
              {prompts.filter((prompt) =>
                !prompt.requires || prompt.requires.every((permission) => permissions.has(permission))
              ).map((prompt) => (
                <button
                  type="button"
                  key={prompt.label}
                  onClick={() => void submit(undefined, prompt.prompt)}
                  disabled={threadBusy}
                >
                  {prompt.label}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((message) => (
          <div className="corral-workspace-message-group" key={message.id}>
            <article className={`corral-workspace-message is-${message.role}`}>
              <span>{message.role === "user" ? "You" : "Corral"}</span>
              <p>{message.content}</p>
              {Boolean(message.leadLinks?.length) && (
                <AgentLeadChoices
                  leads={message.leadLinks!}
                  onSelect={onInspectLead}
                />
              )}
              {message.role === "assistant" && !message.leadLinks?.length && (!message.action || message.action.kind === "none") && (
                <AssistantResultActions content={message.content} permissions={permissions} onNavigate={onNavigate} />
              )}
              {Boolean(message.contextWarnings?.length) && (
                <p role="status">Some workspace information could not be loaded or was limited: {message.contextWarnings!.join(" ")}</p>
              )}
            </article>
            {message.action && message.action.kind !== "none" && (
              <details
                className="corral-workspace-action"
                open={!["applied", "dismissed"].includes(message.actionStatus ?? "")}
                aria-label={
                  message.action.kind === "send_email"
                    ? "Review email draft"
                    : "Proposed action"
                }
              >
                <summary className="corral-workspace-action-heading">
                  <strong>{message.action.title || "Review proposed action"}</strong>
                  <p>{message.action.summary}</p>
                </summary>
                {message.action.kind === "open_deal_desk" && (
                  <p className="corral-workspace-readonly-note">
                    Read-only navigation. Opening the Deal Desk does not create,
                    approve, or send a quote or proposal.
                  </p>
                )}
                {message.action.kind === "send_email" ? (
                  <div className="corral-workspace-email-review">
                    <label>
                      From
                      {sendAccounts.length > 1 ? (
                        <select
                          value={selectedSendAccountId}
                          onChange={(event) =>
                            setSelectedSendAccountId(event.target.value)
                          }
                          disabled={
                            message.actionStatus === "sending" ||
                            message.actionStatus === "applied"
                          }
                        >
                          {sendAccounts.map((account) => (
                            <option key={account.id} value={account.id}>
                              {account.account_name} ({account.sender_email})
                            </option>
                          ))}
                        </select>
                      ) : (
                        <span>
                          {sendAccounts[0]
                            ? `${sendAccounts[0].account_name} (${sendAccounts[0].sender_email})`
                            : sendAccountsLoading
                              ? "Checking Gmail access…"
                              : "No sendable Gmail account"}
                        </span>
                      )}
                    </label>
                    <label>
                      To
                      <input
                        type="text"
                        maxLength={1024}
                        value={message.action.email_recipient ?? ""}
                        onChange={(event) =>
                          updateEmailDraft(
                            message.id,
                            "email_recipient",
                            event.target.value,
                          )
                        }
                        disabled={
                          message.actionStatus === "sending" ||
                          message.actionStatus === "applied"
                        }
                      />
                    </label>
                    <label>
                      Cc (optional)
                      <input
                        type="text"
                        maxLength={1024}
                        placeholder="name@example.com, another@example.com"
                        value={message.action.email_cc ?? ""}
                        onChange={(event) =>
                          updateEmailDraft(
                            message.id,
                            "email_cc",
                            event.target.value,
                          )
                        }
                        disabled={
                          message.actionStatus === "sending" ||
                          message.actionStatus === "applied"
                        }
                      />
                      <small className="corral-workspace-recipient-hint">Separate addresses with commas · up to 10 people</small>
                    </label>
                    <label>
                      Bcc (optional)
                      <input
                        type="text"
                        maxLength={1024}
                        placeholder="name@example.com, another@example.com"
                        value={message.action.email_bcc ?? ""}
                        onChange={(event) =>
                          updateEmailDraft(
                            message.id,
                            "email_bcc",
                            event.target.value,
                          )
                        }
                        disabled={
                          message.actionStatus === "sending" ||
                          message.actionStatus === "applied"
                        }
                      />
                      <small className="corral-workspace-recipient-hint">Separate addresses with commas · up to 10 people</small>
                    </label>
                    <label>
                      Subject
                      <input
                        type="text"
                        maxLength={180}
                        value={message.action.email_subject ?? ""}
                        onChange={(event) =>
                          updateEmailDraft(
                            message.id,
                            "email_subject",
                            event.target.value,
                          )
                        }
                        disabled={
                          message.actionStatus === "sending" ||
                          message.actionStatus === "applied"
                        }
                      />
                    </label>
                    <div className="corral-workspace-email-rich">
                      <span className="email-field-label">Message</span>
                      <div className="email-format-toolbar" role="toolbar" aria-label="Message formatting">
                        <button type="button" aria-label="Bold" title="Bold" disabled={message.actionStatus === "sending" || message.actionStatus === "applied"} onMouseDown={(event) => event.preventDefault()} onClick={() => formatEmailBody(message.id, "bold")}><strong>B</strong></button>
                        <button type="button" aria-label="Italic" title="Italic" disabled={message.actionStatus === "sending" || message.actionStatus === "applied"} onMouseDown={(event) => event.preventDefault()} onClick={() => formatEmailBody(message.id, "italic")}><em>I</em></button>
                        <button type="button" aria-label="Underline" title="Underline" disabled={message.actionStatus === "sending" || message.actionStatus === "applied"} onMouseDown={(event) => event.preventDefault()} onClick={() => formatEmailBody(message.id, "underline")}><u>U</u></button>
                        <button type="button" aria-label="Bulleted list" title="Bulleted list" disabled={message.actionStatus === "sending" || message.actionStatus === "applied"} onMouseDown={(event) => event.preventDefault()} onClick={() => formatEmailBody(message.id, "insertUnorderedList")}>• List</button>
                        <button type="button" aria-label="Numbered list" title="Numbered list" disabled={message.actionStatus === "sending" || message.actionStatus === "applied"} onMouseDown={(event) => event.preventDefault()} onClick={() => formatEmailBody(message.id, "insertOrderedList")}>1. List</button>
                      </div>
                      <div
                        ref={(element) => {
                          if (!element) {
                            emailEditorsRef.current.delete(message.id);
                            return;
                          }
                          emailEditorsRef.current.set(message.id, element);
                          if (element.dataset.messageId !== message.id) {
                            element.innerText = message.action?.email_body ?? "";
                            element.dataset.messageId = message.id;
                          }
                        }}
                        className="email-rich-editor-input"
                        contentEditable={message.actionStatus !== "sending" && message.actionStatus !== "applied"}
                        role="textbox"
                        aria-label="Message"
                        aria-multiline="true"
                        aria-required="true"
                        data-placeholder="Write your message…"
                        onInput={(event) => updateEmailBody(message.id, event.currentTarget)}
                        onPaste={(event) => {
                          event.preventDefault();
                          document.execCommand("insertText", false, event.clipboardData.getData("text/plain"));
                          updateEmailBody(message.id, event.currentTarget);
                        }}
                      />
                      {(message.action.email_body?.length ?? 0) > 8000 && <small className="corral-workspace-email-unavailable" role="alert">Keep the message under 8,000 characters to send it.</small>}
                    </div>
                    {!canSendEmail && (
                      <p className="corral-workspace-email-unavailable">
                        You need lead-edit access to send email.
                      </p>
                    )}
                    {canSendEmail && sendAccountsError && (
                      <p
                        className="corral-workspace-email-unavailable"
                        role="alert"
                      >
                        {sendAccountsError}
                      </p>
                    )}
                    {canSendEmail &&
                      !sendAccountsLoading &&
                      !sendAccountsError &&
                      !sendAccounts.length && (
                        <p className="corral-workspace-email-unavailable">
                          Connect a verified Gmail account with send access before
                          sending.
                        </p>
                      )}
                    {!message.action.email_recipient?.trim() && (
                      <p className="corral-workspace-email-unavailable">
                        Add a recipient to enable sending.
                      </p>
                    )}
                  </div>
                ) : (
                  <>
                    {(message.action.target_id ||
                      message.action.email ||
                      message.action.role ||
                      message.action.enabled !== null) && (
                      <dl>
                        {message.action.target_id && (
                          <>
                            <dt>Target</dt>
                            <dd>
                              {["open_lead", "open_deal_desk", "update_lead_profile", "add_lead_note", "record_lead_progress", "reevaluate_lead"].includes(message.action.kind)
                                ? message.action.target_label ?? "Selected lead"
                                : message.action.target_label
                                ? `${message.action.target_label} (${message.action.target_id})`
                                : message.action.target_id}
                            </dd>
                          </>
                        )}
                        {message.action.email && (
                          <>
                            <dt>Email</dt>
                            <dd>{message.action.email}</dd>
                          </>
                        )}
                        {message.action.role && (
                          <>
                            <dt>Role</dt>
                            <dd>{message.action.role}</dd>
                          </>
                        )}
                        {message.action.enabled !== null && (
                          <>
                            <dt>New value</dt>
                            <dd>{message.action.enabled ? "On" : "Off"}</dd>
                          </>
                        )}
                      </dl>
                    )}
                    {message.action.kind === "create_lead" && message.actionStatus !== "applied" && message.actionStatus !== "dismissed" && (
                      <AssistantLeadDraft
                        value={leadCreationDraft(message.action)}
                        disabled={threadBusy || message.needsVerification || !canApplyWorkspaceAssistantAction(message.action, permissions)}
                        onChange={(value) => setMessages((current) => current.map((item) =>
                          item.id === message.id && item.action ? {
                            ...item, action: { ...item.action, payload_json: JSON.stringify(value) },
                            actionError: undefined,
                          } : item,
                        ))}
                      />
                    )}
                    {message.action.kind === "add_lead_note" && message.actionStatus !== "applied" && message.actionStatus !== "dismissed" && (
                      <AssistantNoteDraft
                        value={workspaceNoteDraft(message.action) ?? ""}
                        disabled={threadBusy || message.needsVerification || !canApplyWorkspaceAssistantAction(message.action, permissions)}
                        onChange={(note) => setMessages((current) => current.map((item) =>
                          item.id === message.id && item.action ? {
                            ...item, action: editWorkspaceNoteDraft(item.action, note),
                            actionError: undefined,
                          } : item,
                        ))}
                      />
                    )}
                    {message.action.kind === "create_automation_draft" && (
                      <AutomationDraftPreview action={message.action} />
                    )}
                    {message.action.payload_json && !["add_lead_note", "create_lead", "create_automation_draft"].includes(message.action.kind) && (
                      <details open={["update_lead_profile", "add_lead_note", "record_lead_progress"].includes(message.action.kind) || undefined}>
                        <summary>Review exact changes</summary>
                        <pre>{message.action.payload_json}</pre>
                      </details>
                    )}
                  </>
                )}
                {message.actionResult && (
                  <p className="corral-workspace-action-result" role="status">
                    {message.actionResult}
                  </p>
                )}
                {message.actionError && (
                  <p className="corral-workspace-error" role="alert">
                    {message.actionError}
                  </p>
                )}
                {message.actionStatus === "dismissed" && (
                  <p className="corral-workspace-action-result">
                    {message.needsVerification ? "Proposal dismissed; no further action taken. Verify whether the earlier attempt completed." : "No change made."}
                  </p>
                )}
                {message.action.kind === "send_email" &&
                  message.actionStatus !== "applied" &&
                  message.actionStatus !== "dismissed" && (
                    <div className="corral-workspace-action-buttons">
                      <button
                        className="primary-button"
                        type="button"
                        disabled={
                          threadBusy ||
                          message.needsVerification ||
                          message.actionStatus === "sending" ||
                          !canApplyWorkspaceAssistantAction(
                            message.action,
                            permissions,
                          ) ||
                          sendAccountsLoading ||
                          !sendAccounts.length ||
                          !selectedSendAccountId ||
                          !message.action.email_recipient?.trim() ||
                          !message.action.email_subject?.trim() ||
                          !message.action.email_body?.trim() ||
                          message.action.email_body.length > 8000
                        }
                        onClick={() =>
                          void handleSendEmail(message.id, message.action!)
                        }
                      >
                        {message.actionStatus === "sending"
                          ? "Sending…"
                          : "Send email"}
                      </button>
                      <button
                        className="secondary-button"
                        type="button"
                        disabled={
                          message.actionStatus === "sending" || busy
                        }
                        onClick={() => dismissAction(message.id)}
                      >
                        Not now
                      </button>
                    </div>
                  )}
                {message.action.kind !== "send_email" &&
                  message.actionStatus !== "applied" &&
                  message.actionStatus !== "dismissed" && (
                    <div className="corral-workspace-action-buttons">
                      <button
                        className="primary-button"
                        type="button"
                        disabled={
                          threadBusy ||
                          message.needsVerification ||
                          message.actionStatus === "applying" ||
                          (message.action.kind === "add_lead_note" && !workspaceNoteDraft(message.action)?.trim()) ||
                          !canApplyWorkspaceAssistantAction(message.action, permissions)
                        }
                        onClick={() =>
                          void handleAction(message.id, message.action!)
                        }
                      >
                        {message.actionStatus === "applying"
                          ? "Applying…"
                          : message.action.kind === "open_module"
                            ? `Open ${message.action.area ? ASSISTANT_AREAS[message.action.area].label : "page"}`
                            : message.action.kind === "open_lead"
                              ? "Open lead"
                              : message.action.kind === "open_deal_desk"
                                ? "Open Deal Desk"
                            : message.action.kind === "complete_lead_manual_review"
                              ? "Complete review & reroute"
                            : message.action.kind === "add_lead_note" ? "Save note"
                            : message.action.kind === "create_lead" ? "Create lead"
                            : message.action.kind === "create_automation_draft" ? "Save as paused draft"
                            : message.action.kind === "enroll_lead_in_automation" ? "Enroll lead"
                            : "Approve and apply"}
                      </button>
                      {message.action.kind !== "open_module" && (
                        <button
                          className="secondary-button"
                          type="button"
                          disabled={message.actionStatus === "applying"}
                          onClick={() => dismissAction(message.id)}
                        >
                          {["add_lead_note", "create_lead"].includes(message.action.kind) ? "Discard draft" : "Not now"}
                        </button>
                      )}
                    </div>
                  )}
              </details>
            )}
          </div>
        ))}
        {busy && (
          <p className="corral-workspace-thinking" role="status">
            Corral is thinking…
          </p>
        )}
        {error && (
          <p className="corral-workspace-error" role="alert">
            {error}
          </p>
        )}
        </>
        )}
      </div>
      <form
        className="corral-workspace-composer"
        onSubmit={(event) => void submit(event)}
      >
        <label htmlFor="corral-workspace-prompt" className="v-sr">Message Ask Corral</label>
        <textarea
          id="corral-workspace-prompt"
          rows={3}
          maxLength={3000}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void submit();
            }
          }}
          placeholder="Ask a question or describe something to create…"
        />
        <div className="corral-workspace-composer-footer">
          <small><kbd>↵</kbd> send · <kbd>⇧</kbd><kbd>↵</kbd> new line</small>
          <button
            className="primary-button"
            type="submit"
            disabled={threadBusy || !draft.trim()}
          >
            {busy ? "Thinking…" : "Send"}
          </button>
        </div>
      </form>
    </section>
  );
}
