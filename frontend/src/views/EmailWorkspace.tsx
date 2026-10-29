import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  deleteWorkspaceEmailDraft,
  getActiveWorkspace,
  getWorkspaceEmailDraft,
  getGmailMailboxThread,
  listGmailMailboxMessages,
  listIntegrations,
  listWorkspaceEmailDrafts,
} from "../api";
import { EmailComposer, type GmailComposerSeed } from "../components/EmailComposer";
import { getProviderEmailPreview, ProviderEmailBody } from "../components/ProviderEmailBody";
import type {
  GmailMailboxMessage,
  GmailThreadMessage,
  ProviderConnection,
  WorkspaceEmailDraft,
  WorkspaceEmailDraftSummary,
} from "../types";

const GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly";
const MAILBOX_CACHE_FRESH_MS = 60_000;
const accountCache = new Map<string, ProviderConnection[]>();
const mailboxCache = new Map<string, { messages: GmailMailboxMessage[]; nextPageToken: string | null; fetchedAt: number }>();
const threadCache = new Map<string, { messages: GmailThreadMessage[]; fetchedAt: number }>();

function remember<T>(cache: Map<string, T>, key: string, value: T) {
  cache.delete(key);
  cache.set(key, value);
  if (cache.size > 30) cache.delete(cache.keys().next().value!);
}

function emailDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function EmailWorkspace({
  canSend,
  onOpenIntegrations,
}: {
  canSend: boolean;
  onOpenIntegrations: () => void;
}) {
  const [accounts, setAccounts] = useState<ProviderConnection[]>([]);
  const [connectionId, setConnectionId] = useState("");
  const [folder, setFolder] = useState<"inbox" | "sent" | "drafts" | "spam" | "trash" | "archive">("inbox");
  const [messages, setMessages] = useState<GmailMailboxMessage[]>([]);
  const [selected, setSelected] = useState<GmailMailboxMessage | null>(null);
  const [thread, setThread] = useState<GmailThreadMessage[]>([]);
  const [expandedMessageIds, setExpandedMessageIds] = useState<Set<string>>(
    new Set(),
  );
  const [queryInput, setQueryInput] = useState("");
  const [query, setQuery] = useState("");
  const [pageToken, setPageToken] = useState<string | null>(null);
  const [nextPageToken, setNextPageToken] = useState<string | null>(null);
  const [loadingAccounts, setLoadingAccounts] = useState(true);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [loadingThread, setLoadingThread] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const [threadRevision, setThreadRevision] = useState(0);
  const [composeSeed, setComposeSeed] = useState<GmailComposerSeed | null>(null);
  const [composerRevision, setComposerRevision] = useState(0);
  const [drafts, setDrafts] = useState<WorkspaceEmailDraftSummary[]>([]);
  const [activeDraft, setActiveDraft] = useState<WorkspaceEmailDraft | null>(null);
  const [draftRevision, setDraftRevision] = useState(0);
  const lastMailboxKey = useRef("");
  const backgroundRefresh = useRef(false);
  const lastThreadRefresh = useRef(0);

  useEffect(() => {
    let alive = true;
    const workspaceId = getActiveWorkspace();
    const cachedAccounts = accountCache.get(workspaceId);
    if (cachedAccounts) {
      setAccounts(cachedAccounts);
      setConnectionId((current) => cachedAccounts.some((account) => account.id === current) ? current : cachedAccounts[0]?.id ?? "");
      setLoadingAccounts(false);
    }
    listIntegrations().then((cards) => {
      if (!alive) return;
      const gmail = cards.find((card) => card.provider === "gmail");
      const rows = gmail?.connections.length ? gmail.connections : gmail?.connection ? [gmail.connection] : [];
      const available = rows.filter((account) =>
        account.is_active && account.status === "verified" && account.scopes.includes(GMAIL_READONLY_SCOPE),
      );
      accountCache.set(workspaceId, available);
      setAccounts(available);
      setConnectionId((current) => available.some((account) => account.id === current) ? current : available[0]?.id ?? "");
      setLoadingAccounts(false);
    }).catch((reason) => {
      if (!alive) return;
      setError(reason instanceof Error ? reason.message : "Connected Gmail accounts could not be loaded.");
      setLoadingAccounts(false);
    });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (!connectionId) {
      lastMailboxKey.current = "";
      setMessages([]);
      setSelected(null);
      setThread([]);
      setExpandedMessageIds(new Set());
      setNextPageToken(null);
      return;
    }
    const controller = new AbortController();
    const mailboxKey = JSON.stringify([getActiveWorkspace(), connectionId, folder, query, pageToken]);
    const changedMailbox = lastMailboxKey.current !== mailboxKey;
    lastMailboxKey.current = mailboxKey;
    const cached = mailboxCache.get(mailboxKey);
    const quiet = (backgroundRefresh.current && !changedMailbox) || Boolean(changedMailbox && cached);
    backgroundRefresh.current = false;
    if (!quiet) setLoadingMessages(true);
    if (!quiet) setError(null);
    if (changedMailbox && cached) {
      setMessages(cached.messages);
      setSelected(cached.messages[0] ?? null);
      setThread([]);
      setExpandedMessageIds(new Set());
      setNextPageToken(cached.nextPageToken);
    } else if (changedMailbox) {
      setSelected(null);
      setThread([]);
      setExpandedMessageIds(new Set());
    }
    // Navigating back to a recently opened mailbox should show its saved rows
    // immediately. The visible 60-second poll and manual refresh still check Gmail.
    if (changedMailbox && cached && Date.now() - cached.fetchedAt < MAILBOX_CACHE_FRESH_MS) {
      setLoadingMessages(false);
      return () => controller.abort();
    }
    listGmailMailboxMessages(connectionId, folder, query, pageToken, controller.signal).then((page) => {
      if (controller.signal.aborted) return;
      remember(mailboxCache, mailboxKey, { messages: page.messages, nextPageToken: page.next_page_token, fetchedAt: Date.now() });
      const changedRows = JSON.stringify(page.messages) !== JSON.stringify(changedMailbox && cached ? cached.messages : messages);
      if (changedRows) {
        setMessages(page.messages);
        setSelected((current) => changedMailbox && !cached
          ? page.messages[0] ?? null
          : page.messages.find((message) => message.message_id === current?.message_id) ?? page.messages[0] ?? null);
        if (quiet && !changedMailbox && selected?.thread_id) {
          const selectedThreadKey = JSON.stringify([getActiveWorkspace(), connectionId, selected.thread_id]);
          threadCache.delete(selectedThreadKey);
          setThreadRevision((current) => current + 1);
        }
      }
      setNextPageToken(page.next_page_token);
      setLoadingMessages(false);
    }).catch((reason) => {
      if (!controller.signal.aborted) {
        if (!quiet) setError(reason instanceof Error ? reason.message : "Mailbox messages could not be loaded.");
        setLoadingMessages(false);
      }
    });
    return () => controller.abort();
  }, [connectionId, folder, query, pageToken, revision]);

  useEffect(() => {
    if (!connectionId) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") {
        backgroundRefresh.current = true;
        setRevision((current) => current + 1);
      }
    }, 60_000);
    return () => window.clearInterval(timer);
  }, [connectionId]);

  useEffect(() => {
    if (!canSend || folder !== "drafts") return;
    let alive = true;
    listWorkspaceEmailDrafts().then((rows) => {
      if (alive) setDrafts(rows);
    }).catch((reason) => {
      if (alive) setError(reason instanceof Error ? reason.message : "Saved drafts could not be loaded.");
    });
    return () => { alive = false; };
  }, [canSend, folder, draftRevision]);

  useEffect(() => {
    if (!connectionId || !selected?.thread_id) {
      setThread([]);
      setExpandedMessageIds(new Set());
      return;
    }
    const controller = new AbortController();
    const key = JSON.stringify([getActiveWorkspace(), connectionId, selected.thread_id]);
    const cached = threadCache.get(key);
    const refreshRequested = threadRevision !== lastThreadRefresh.current;
    lastThreadRefresh.current = threadRevision;
    if (cached) {
      setThread(cached.messages);
      setLoadingThread(false);
    }
    if (!cached) {
      setLoadingThread(true);
      setThread([]);
      setExpandedMessageIds(new Set());
    }
    if (cached && !refreshRequested && Date.now() - cached.fetchedAt < MAILBOX_CACHE_FRESH_MS) {
      return () => controller.abort();
    }
    getGmailMailboxThread(connectionId, selected.thread_id, controller.signal).then((result) => {
      if (!controller.signal.aborted) {
        remember(threadCache, key, { messages: result.messages, fetchedAt: Date.now() });
        setThread((current) => JSON.stringify(current) === JSON.stringify(result.messages) ? current : result.messages);
        setLoadingThread(false);
      }
    }).catch((reason) => {
      if (!controller.signal.aborted) {
        if (!cached) setError(reason instanceof Error ? reason.message : "Email thread could not be loaded.");
        setLoadingThread(false);
      }
    });
    return () => controller.abort();
  }, [connectionId, selected?.thread_id, threadRevision]);

  function submitSearch(event: FormEvent) {
    event.preventDefault();
    setPageToken(null);
    setQuery(queryInput.trim());
  }

  function beginReplyTo(message: GmailThreadMessage) {
    if (!selected || message.direction !== "inbound") return;
    setActiveDraft(null);
    const subject = message.subject || selected.subject;
    setComposeSeed({
      recipient: message.sender,
      subject: /^re:/i.test(subject) ? subject : `Re: ${subject}`,
      threadId: message.thread_id || selected.thread_id,
      inReplyTo: message.rfc_message_id || undefined,
      connectionId,
    });
    setComposerRevision((current) => current + 1);
  }

  function beginReply() {
    const lastInbound = [...thread].reverse().find((message) => message.direction === "inbound");
    if (lastInbound) beginReplyTo(lastInbound);
  }

  function beginCompose() {
    setActiveDraft(null);
    setComposeSeed({ connectionId });
    setComposerRevision((current) => current + 1);
  }

  function toggleThreadMessage(messageId: string) {
    setExpandedMessageIds((current) => {
      const next = new Set(current);
      if (next.has(messageId)) next.delete(messageId);
      else next.add(messageId);
      return next;
    });
  }

  const allThreadMessagesExpanded =
    thread.length > 0 &&
    thread.every((message) => expandedMessageIds.has(message.message_id));

  const activeAccount = accounts.find((account) => account.id === connectionId);
  const folderLabel = {
    inbox: "Inbox",
    sent: "Sent",
    drafts: "Drafts",
    spam: "Spam",
    trash: "Trash",
    archive: "Archive",
  }[folder];

  return <div className="page-view operational-page email-workspace-page">
    <header className="page-hero email-workspace-hero">
      <div><span className="eyebrow">Workspace / Email</span><h1>Shared inbox</h1><p>Read and send email from connected Gmail accounts, with mailbox selection kept explicit.</p></div>
      <button className="primary-button" type="button" onClick={beginCompose} disabled={!canSend}>New draft</button>
    </header>
    {error && <div className="notice" role="alert">{error}</div>}
    {composeSeed && <EmailComposer
      key={composerRevision}
      accounts={accounts}
      canSend={canSend}
      seed={composeSeed}
      draft={activeDraft ?? undefined}
      onClose={() => { setComposeSeed(null); setActiveDraft(null); }}
      onSaved={() => {
        setComposeSeed(null);
        setActiveDraft(null);
        setFolder("drafts");
        setDraftRevision((current) => current + 1);
      }}
      onSent={() => {
        setComposeSeed(null);
        setActiveDraft(null);
        setRevision((current) => current + 1);
        setThreadRevision((current) => current + 1);
        setDraftRevision((current) => current + 1);
      }}
    />}
    <section className="email-mailbox-toolbar panel">
      <label>Gmail account
        <select value={connectionId} onChange={(event) => {
          setConnectionId(event.target.value);
          setPageToken(null);
          setSelected(null);
          setThread([]);
        }} disabled={loadingAccounts || !accounts.length}>
          {accounts.map((account) => <option value={account.id} key={account.id}>{account.account_name}</option>)}
        </select>
      </label>
      <div className="email-folder-switch" role="group" aria-label="Mailbox folder">
        {([
          ["inbox", "Inbox"],
          ["sent", "Sent"],
          ["drafts", "Drafts"],
          ["spam", "Spam"],
          ["trash", "Trash"],
          ["archive", "Archive"],
        ] as const).map(([value, label]) => (
          <button
            key={value}
            type="button"
            className={folder === value ? "is-active" : ""}
            aria-pressed={folder === value}
            onClick={() => { setFolder(value); setPageToken(null); }}
          >
            {label}
          </button>
        ))}
      </div>
      <form className="email-search" onSubmit={submitSearch}>
        <label className="sr-only" htmlFor="shared-email-search">Search email</label>
        <input id="shared-email-search" value={queryInput} onChange={(event) => setQueryInput(event.target.value)} placeholder="Search this mailbox" />
        <button className="secondary-button" type="submit">Search</button>
      </form>
      <button className="button-quiet" type="button" onClick={() => { backgroundRefresh.current = false; setRevision((current) => current + 1); }} disabled={!connectionId || loadingMessages}>Refresh Gmail</button>
    </section>
    {loadingAccounts && <div className="email-empty-state"><span>Loading connected Gmail accounts…</span></div>}
    {!loadingAccounts && accounts.length === 0 && <div className="email-empty-state"><strong>No verified Gmail account is connected.</strong><span>Connect one or more Gmail accounts to read the shared inbox.</span><button className="secondary-button" type="button" onClick={onOpenIntegrations}>Open Integrations</button></div>}
    {folder === "drafts" && canSend && <section className="panel email-saved-drafts" aria-label="Corral drafts">
      <header><div><span className="eyebrow">Private to you</span><h2>Corral drafts</h2></div><span>{drafts.length} saved</span></header>
      <p>Saved in this workspace until you send or delete them. Gmail drafts appear in the mailbox list below.</p>
      {drafts.length === 0 ? <p className="email-list-empty">No saved Corral drafts yet. Select New draft to write one.</p> : <ul>
        {drafts.map((draft) => <li key={draft.id}>
          <button type="button" className="email-draft-open" onClick={() => {
            getWorkspaceEmailDraft(draft.id).then((full) => {
              setActiveDraft(full);
              setComposeSeed({ connectionId: full.data.connection_id ?? undefined });
              setComposerRevision((current) => current + 1);
            }).catch((reason) => setError(reason instanceof Error ? reason.message : "Draft could not be opened."));
          }}><strong>{draft.subject || "(no subject)"}</strong><span>{draft.recipient || "Recipient not set"} · {emailDate(draft.updated_at)}</span></button>
          <button type="button" className="button-quiet" aria-label={`Delete draft ${draft.subject || "without subject"}`} onClick={() => {
            if (!window.confirm("Delete this saved draft? This cannot be undone.")) return;
            deleteWorkspaceEmailDraft(draft.id).then(() => setDraftRevision((current) => current + 1)).catch((reason) => setError(reason instanceof Error ? reason.message : "Draft could not be deleted."));
          }}>Delete</button>
        </li>)}
      </ul>}
    </section>}
    {!loadingAccounts && accounts.length > 0 && <div className="email-workspace-grid">
       <section className="email-message-list panel" aria-label={`${folderLabel} messages`}>
         <header><div><span className="eyebrow">{folderLabel}</span><h2>{activeAccount?.account_name ?? "Mailbox"}</h2></div><span>{messages.length} shown</span></header>
        {loadingMessages && <p className="email-status">Loading messages…</p>}
        {!loadingMessages && messages.length === 0 && <div className="email-list-empty">No email matches this view.</div>}
        <div className="email-message-list-rows">
          {messages.map((message) => <button
            type="button"
            key={`${message.message_id}-${message.thread_id}`}
            className={`email-message-row${selected?.message_id === message.message_id ? " is-selected" : ""}${message.unread ? " is-unread" : ""}`}
            onClick={() => setSelected(message)}
          >
            <span className="email-row-top"><strong>{message.sender_name || message.sender || "Unknown sender"}</strong><time>{emailDate(message.sent_at)}</time></span>
            <b>{message.subject || "(no subject)"}</b>
            <span className="email-row-snippet">{message.snippet || "Open to read this message."}</span>
          </button>)}
        </div>
        {nextPageToken && <button className="secondary-button email-next-page" type="button" onClick={() => setPageToken(nextPageToken)} disabled={loadingMessages}>Next page</button>}
      </section>
      <section className="email-thread-detail panel" aria-label="Email thread">
        {!selected && <div className="email-list-empty">Select an email to read the conversation.</div>}
        {selected && <>
          <header className="email-thread-heading">
            <div><span className="eyebrow">{selected.direction === "outbound" ? "Sent email" : "Inbox conversation"}</span><h2>{selected.subject || "(no subject)"}</h2></div>
            <div className="email-thread-heading-actions">
              {thread.length > 0 && <button className="button-quiet" type="button" onClick={() => {
                setExpandedMessageIds(
                  allThreadMessagesExpanded
                    ? new Set()
                    : new Set(thread.map((message) => message.message_id)),
                );
              }}>{allThreadMessagesExpanded ? "Collapse all" : "Expand all"}</button>}
              {thread.some((message) => message.direction === "inbound") && <button className="secondary-button" type="button" onClick={beginReply}>Reply</button>}
            </div>
          </header>
          {loadingThread && <p className="email-status">Loading conversation…</p>}
          <div className="email-thread-messages">
            {thread.map((message, index) => {
              const expanded = expandedMessageIds.has(message.message_id);
              const body = message.body || "";
              const excerpt = getProviderEmailPreview(body);
              const bodyId = `email-thread-message-body-${index}`;
              return <article className={`email-thread-message${message.direction === "outbound" ? " is-outbound" : ""}`} key={message.message_id}>
                <button
                  className="email-thread-message-toggle"
                  type="button"
                  aria-expanded={expanded}
                  aria-controls={bodyId}
                  aria-label={`${expanded ? "Collapse" : "Expand"} message from ${message.sender_name || message.sender || "Unknown sender"}`}
                  onClick={() => toggleThreadMessage(message.message_id)}
                >
                  <span className="email-thread-message-summary-header">
                    <span className="email-thread-message-sender">
                      <strong>{message.sender_name || message.sender || "Unknown sender"}</strong>
                      <span>{message.sender}</span>
                    </span>
                    <time>{emailDate(message.sent_at)}</time>
                  </span>
                  <span className="email-thread-message-summary-footer">
                    {!expanded && <span className="email-thread-message-preview">{excerpt}</span>}
                    <span className="email-thread-message-toggle-label">{expanded ? "Collapse" : "Expand"}</span>
                  </span>
                </button>
                {message.direction === "inbound" && <div className="email-thread-message-actions">
                  <button
                    className="button-quiet email-message-reply-button"
                    type="button"
                    aria-label={`Reply to ${message.sender_name || message.sender || "this message"}`}
                    onClick={() => beginReplyTo(message)}
                  >Reply</button>
                </div>}
                {expanded && <div className="email-thread-message-body" id={bodyId}>
                  <div className="email-thread-addresses"><span>To</span>{message.recipients || "Not listed"}</div>
                  <ProviderEmailBody content={body} />
                </div>}
              </article>;
            })}
          </div>
        </>}
      </section>
    </div>}
  </div>;
}
