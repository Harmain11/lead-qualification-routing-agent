import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  chatWithWorkspaceAssistant,
  listGmailSendAccounts,
  getLead,
  sendGmailEmail,
  type WorkspaceAssistantAction,
  type WorkspaceAssistantArea,
  type WorkspaceAssistantLeadLink,
} from "../api";
import type { GmailSendAccount, Lead } from "../types";
import { createClientId } from "../clientIds";
import { assistantConversation } from "../assistantConversation";
import { isDirectPhoneCallRequest } from "./workspaceAssistantIntents";
import {
  applyWorkspaceAssistantAction,
  applyWorkspaceEmailSignature,
  canOpenAssistantArea,
  canApplyWorkspaceAssistantAction,
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
      const result = await chatWithWorkspaceAssistant(
        assistantConversation(nextMessages),
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
      <div
        className="corral-workspace-transcript"
        ref={transcriptRef}
        aria-live="polite"
      >
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
              <p>{message.content}</p>
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
                          ? `Confirm and open suggested lead ${lead.name}`
                          : `Open lead ${lead.name}`
                      }
                      onClick={() => onInspectLead(lead.id)}
                    >
                      <strong>{lead.name}</strong>
                      {lead.company && <span>{lead.company}</span>}
                      <small>
                        {lead.suggested
                          ? "Suggested match · open to confirm"
                          : "Open lead"}
                      </small>
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
                    {message.action.payload_json && (
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
                            : "Approve and apply"}
                      </button>
                      {message.action.kind !== "open_module" && (
                        <button
                          className="secondary-button"
                          type="button"
                          disabled={message.actionStatus === "applying"}
                          onClick={() => dismissAction(message.id)}
                        >
                          Not now
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
