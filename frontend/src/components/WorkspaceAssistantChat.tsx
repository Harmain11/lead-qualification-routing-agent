import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  chatWithWorkspaceAssistant,
  type WorkspaceAssistantAction,
  type WorkspaceAssistantArea,
} from "../api";
import { createClientId } from "../clientIds";
import {
  applyWorkspaceAssistantAction,
  canApplyWorkspaceAssistantAction,
  loadWorkspaceAssistantContext,
} from "./workspaceAssistantActions";

type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  action?: WorkspaceAssistantAction;
  actionStatus?: "applying" | "applied" | "dismissed";
  actionResult?: string;
  actionError?: string;
};

const prompts = [
  "Explain how Corral qualifies and routes leads",
  "Summarize this month’s workspace analytics",
  "Review our active automations and recommend improvements",
  "Help me update our ICP or workspace settings",
  "Show me integration and team access options",
];

type Props = {
  permissions: ReadonlySet<string>;
  onNavigate: (area: WorkspaceAssistantArea) => void;
};

export function WorkspaceAssistantChat({ permissions, onNavigate }: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const transcriptRef = useRef<HTMLDivElement>(null);

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
    try {
      const result = await chatWithWorkspaceAssistant(
        nextMessages
          .slice(-16)
          .map(({ role, content: messageContent }) => ({
            role,
            content: messageContent,
          })),
        await loadWorkspaceAssistantContext(permissions),
      );
      setMessages((current) => [
        ...current,
        {
          id: createClientId(),
          role: "assistant",
          content: result.reply,
          action: result.action?.kind !== "none" ? result.action : undefined,
        },
      ]);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Corral Agent could not answer. Try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function handleAction(messageId: string, action: WorkspaceAssistantAction) {
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
      );
      setMessages((current) =>
        current.map((message) =>
          message.id === messageId
            ? { ...message, actionStatus: "applied", actionResult: result }
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
                    : "The proposed action could not be applied.",
              }
            : message,
        ),
      );
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
              Ask questions about workspace analytics, automations, ICP,
              settings, integrations, and team access. Corral Agent can prepare
              changes, but it will not apply them until you approve each one.
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
            </article>
            {message.action && (
              <section className="corral-workspace-action" aria-label="Proposed action">
                <div>
                  <strong>{message.action.title || "Review proposed action"}</strong>
                  <p>{message.action.summary}</p>
                </div>
                {(message.action.target_id ||
                  message.action.email ||
                  message.action.role ||
                  message.action.enabled !== null) && (
                  <dl>
                    {message.action.target_id && (
                      <>
                        <dt>Target</dt>
                        <dd>
                          {message.action.target_label
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
                  <details>
                    <summary>Review exact changes</summary>
                    <pre>{message.action.payload_json}</pre>
                  </details>
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
                {message.actionStatus !== "applied" &&
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
                            ? `Open ${message.action.area ?? "workspace"}`
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
              </section>
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
