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
import { AssistantMarkdown } from "./AssistantMarkdown";
import { AssistantNoteDraft } from "./AssistantNoteDraft";
import { AssistantLeadDraft } from "./AssistantLeadDraft";
import { isDirectPhoneCallRequest } from "./workspaceAssistantIntents";
import {
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

const prompts = [
  "Find a lead and summarize recent emails, calls, or meetings",
  "Explain how Corral qualifies and routes leads",
  "Summarize this month’s workspace analytics",
  "Review our active automations and recommend improvements",
  "Help me update our ICP or workspace settings",
  "Show me integration and team access options",
  "Draft an email for me to review before sending",
  "Update a lead’s details or add a note after review",
];

type Props = {
  permissions: ReadonlySet<string>;
  onNavigate: (area: WorkspaceAssistantArea) => void;
  onInspectLead: (leadId: string) => void;
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
    if (busy || id === conversationIdRef.current) {
      setHistoryOpen(false);
      return;
    }
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
      setHistoryOpen(false);
      setHistoryError("");
    } catch (reason) {
      setHistoryError(reason instanceof Error ? reason.message : "That conversation could not be opened.");
    }
  }

  async function startNewConversation() {
    if (busy) return;
    try {
      await flushActiveConversation();
      conversationIdRef.current = null;
      setConversationId(null);
      setMessages([]);
      setHistoryOpen(false);
      setError("");
      setHistoryError("");
    } catch {
      setHistoryError("The previous chat could not be saved.");
    }
  }

  async function removeConversation(id: string) {
    try {
      await flushActiveConversation();
      await deleteWorkspaceAgentConversation(id);
      setConfirmDeleteId(null);
      setConversations((current) => current.filter((item) => item.id !== id));
      if (id === conversationIdRef.current) {
        conversationIdRef.current = null;
        setConversationId(null);
        setMessages([]);
      }
    } catch (reason) {
      setHistoryError(reason instanceof Error ? reason.message : "That conversation could not be deleted.");
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
    if (!content || busy) return;
    if (messages.length >= 500) {
      setError("This chat reached its history limit. Start a new conversation to continue.");
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
            summary: "Choose the lead or enter a number, then start the call yourself. Corral Agent cannot place calls.",
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
            : "Corral Agent could not answer. Try again.",
        );
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleAction(messageId: string, action: WorkspaceAssistantAction) {
    if (applyingMessageIdsRef.current.has(messageId)) return;
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
      if (action.target_id && ["update_lead_profile", "add_lead_note", "record_lead_progress", "reevaluate_lead"].includes(action.kind) && onLeadUpdated) {
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
    <section className="corral-workspace-chat" aria-label="Chat with Corral Agent">
      <header className="corral-workspace-chat-toolbar">
        <div className="corral-workspace-chat-toolbar__title">
          <strong>
            {conversations.find((conversation) => conversation.id === conversationId)?.title
              || "Corral Agent"}
          </strong>
          {historyError && <span role="status">{historyError}</span>}
        </div>
        <div className="corral-workspace-chat-toolbar__actions">
          <button type="button" className="secondary-button" onClick={() => void startNewConversation()} disabled={busy || historyLoading}>
            New chat
          </button>
          <button
            type="button"
            className="secondary-button"
            aria-pressed={historyOpen}
            onClick={() => setHistoryOpen((open) => !open)}
            disabled={busy || historyLoading}
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
                      disabled={busy}
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
                      disabled={busy}
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
                          disabled={busy}
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
              Search Corral's stored leads, contacts, and synced email, call,
              meeting, and note history alongside analytics, automations, ICP,
              settings, integrations, and team access. Corral Agent retrieves
              relevant records on demand. It will not send or apply changes
              until you approve them.
            </p>
            <div className="corral-workspace-prompts">
              {prompts.map((prompt) => (
                <button
                  type="button"
                  key={prompt}
                  onClick={() => void submit(undefined, prompt)}
                  disabled={busy}
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((message) => (
          <div className="corral-workspace-message-group" key={message.id}>
            <article className={`corral-workspace-message is-${message.role}`}>
              <span>{message.role === "user" ? "You" : "Corral Agent"}</span>
              {message.role === "assistant"
                ? <AssistantMarkdown content={message.content} />
                : <p>{message.content}</p>}
              {Boolean(message.leadLinks?.length) && (
                <div
                  className="corral-workspace-lead-links"
                  role="group"
                  aria-label="Matching leads"
                >
                  {message.leadLinks!.map((lead) => (
                    <button
                      className="primary-button corral-workspace-lead-link"
                      key={lead.id}
                      type="button"
                      aria-label={
                        lead.suggested
                          ? `Open suggested match ${lead.name}`
                          : `Open lead ${lead.name}`
                      }
                      onClick={() => onInspectLead(lead.id)}
                    >
                      <strong>{lead.name}</strong>
                      {lead.company && (
                        <span className="corral-workspace-lead-company">{lead.company}</span>
                      )}
                    </button>
                  ))}
                </div>
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
                              {["open_lead", "update_lead_profile", "add_lead_note", "record_lead_progress", "reevaluate_lead"].includes(message.action.kind)
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
                        disabled={message.actionStatus === "applying" || !canApplyWorkspaceAssistantAction(message.action, permissions)}
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
                        disabled={message.actionStatus === "applying" || !canApplyWorkspaceAssistantAction(message.action, permissions)}
                        onChange={(note) => setMessages((current) => current.map((item) =>
                          item.id === message.id && item.action ? {
                            ...item, action: editWorkspaceNoteDraft(item.action, note),
                            actionError: undefined,
                          } : item,
                        ))}
                      />
                    )}
                    {message.action.payload_json && !["add_lead_note", "create_lead"].includes(message.action.kind) && (
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
                  <p className="corral-workspace-action-result">No change made.</p>
                )}
                {message.action.kind === "send_email" &&
                  message.actionStatus !== "applied" &&
                  message.actionStatus !== "dismissed" && (
                    <div className="corral-workspace-action-buttons">
                      <button
                        className="primary-button"
                        type="button"
                        disabled={
                          busy ||
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
                          busy ||
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
                            ? message.action.area === "dialer"
                              ? "Open Dialer"
                              : `Open ${message.action.area ?? "workspace"}`
                            : message.action.kind === "open_lead"
                              ? "Open lead"
                            : message.action.kind === "add_lead_note" ? "Save note"
                            : message.action.kind === "create_lead" ? "Create lead"
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
            Corral Agent is thinking…
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
        <label htmlFor="corral-workspace-prompt">Message Corral Agent</label>
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
          <small>Press Enter to send · Shift+Enter for a new line</small>
          <button
            className="primary-button"
            type="submit"
            disabled={busy || !draft.trim()}
          >
            {busy ? "Thinking…" : "Send"}
          </button>
        </div>
      </form>
    </section>
  );
}
