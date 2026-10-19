import { useEffect, useState, type FormEvent } from "react";
import {
  getGmailMailboxThread,
  listGmailMailboxMessages,
  listIntegrations,
} from "../api";
import { EmailComposer, type GmailComposerSeed } from "../components/EmailComposer";
import type {
  GmailMailboxMessage,
  GmailThreadMessage,
  ProviderConnection,
} from "../types";
import "./emailWorkspace.css";

const GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly";

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
  const [folder, setFolder] = useState<"inbox" | "sent">("inbox");
  const [messages, setMessages] = useState<GmailMailboxMessage[]>([]);
  const [selected, setSelected] = useState<GmailMailboxMessage | null>(null);
  const [thread, setThread] = useState<GmailThreadMessage[]>([]);
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

  useEffect(() => {
    let alive = true;
    listIntegrations().then((cards) => {
      if (!alive) return;
      const gmail = cards.find((card) => card.provider === "gmail");
      const rows = gmail?.connections.length ? gmail.connections : gmail?.connection ? [gmail.connection] : [];
      const available = rows.filter((account) =>
        account.is_active && account.status === "verified" && account.scopes.includes(GMAIL_READONLY_SCOPE),
      );
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
      setMessages([]);
      setSelected(null);
      setNextPageToken(null);
      return;
    }
    const controller = new AbortController();
    setLoadingMessages(true);
    setError(null);
    setSelected(null);
    setThread([]);
    listGmailMailboxMessages(connectionId, folder, query, pageToken, controller.signal).then((page) => {
      if (controller.signal.aborted) return;
      setMessages(page.messages);
      setSelected(page.messages[0] ?? null);
      setNextPageToken(page.next_page_token);
      setLoadingMessages(false);
    }).catch((reason) => {
      if (!controller.signal.aborted) {
        setError(reason instanceof Error ? reason.message : "Mailbox messages could not be loaded.");
        setLoadingMessages(false);
      }
    });
    return () => controller.abort();
  }, [connectionId, folder, query, pageToken, revision]);

  useEffect(() => {
    if (!connectionId || !selected?.thread_id) {
      setThread([]);
      return;
    }
    const controller = new AbortController();
    setLoadingThread(true);
    setThread([]);
    getGmailMailboxThread(connectionId, selected.thread_id, controller.signal).then((result) => {
      if (!controller.signal.aborted) {
        setThread(result.messages);
        setLoadingThread(false);
      }
    }).catch((reason) => {
      if (!controller.signal.aborted) {
        setError(reason instanceof Error ? reason.message : "Email thread could not be loaded.");
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

  function beginReply() {
    const lastInbound = [...thread].reverse().find((message) => message.direction === "inbound");
    if (!selected || !lastInbound) return;
    const subject = lastInbound.subject || selected.subject;
    setComposeSeed({
      recipient: lastInbound.sender,
      subject: /^re:/i.test(subject) ? subject : `Re: ${subject}`,
      threadId: lastInbound.thread_id || selected.thread_id,
      inReplyTo: lastInbound.rfc_message_id || undefined,
      connectionId,
    });
    setComposerRevision((current) => current + 1);
  }

  function beginCompose() {
    setComposeSeed({ connectionId });
    setComposerRevision((current) => current + 1);
  }

  const activeAccount = accounts.find((account) => account.id === connectionId);

  return <div className="page-view operational-page email-workspace-page">
    <header className="page-hero email-workspace-hero">
      <div><span className="eyebrow">Workspace / Email</span><h1>Shared inbox</h1><p>Read and send email from connected Gmail accounts, with mailbox selection kept explicit.</p></div>
      <button className="primary-button" type="button" onClick={beginCompose} disabled={!accounts.length}>Compose email</button>
    </header>
    {error && <div className="notice" role="alert">{error}</div>}
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
        <button type="button" className={folder === "inbox" ? "is-active" : ""} aria-pressed={folder === "inbox"} onClick={() => { setFolder("inbox"); setPageToken(null); }}>Inbox</button>
        <button type="button" className={folder === "sent" ? "is-active" : ""} aria-pressed={folder === "sent"} onClick={() => { setFolder("sent"); setPageToken(null); }}>Sent</button>
      </div>
      <form className="email-search" onSubmit={submitSearch}>
        <label className="sr-only" htmlFor="shared-email-search">Search email</label>
        <input id="shared-email-search" value={queryInput} onChange={(event) => setQueryInput(event.target.value)} placeholder="Search this mailbox" />
        <button className="secondary-button" type="submit">Search</button>
      </form>
      <button className="button-quiet" type="button" onClick={() => setRevision((current) => current + 1)} disabled={!connectionId || loadingMessages}>Refresh</button>
    </section>
    {loadingAccounts && <div className="email-empty-state"><span>Loading connected Gmail accounts…</span></div>}
    {!loadingAccounts && accounts.length === 0 && <div className="email-empty-state"><strong>No verified Gmail account is connected.</strong><span>Connect one or more Gmail accounts to read the shared inbox.</span><button className="secondary-button" type="button" onClick={onOpenIntegrations}>Open Integrations</button></div>}
    {!loadingAccounts && accounts.length > 0 && <div className="email-workspace-grid">
      <section className="email-message-list panel" aria-label={`${folder} messages`}>
        <header><div><span className="eyebrow">{folder === "inbox" ? "Inbox" : "Sent mail"}</span><h2>{activeAccount?.account_name ?? "Mailbox"}</h2></div><span>{messages.length} shown</span></header>
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
            {thread.some((message) => message.direction === "inbound") && <button className="secondary-button" type="button" onClick={beginReply}>Reply</button>}
          </header>
          {loadingThread && <p className="email-status">Loading conversation…</p>}
          <div className="email-thread-messages">
            {thread.map((message) => <article className={`email-thread-message${message.direction === "outbound" ? " is-outbound" : ""}`} key={message.message_id}>
              <header><div><strong>{message.sender_name || message.sender || "Unknown sender"}</strong><span>{message.sender}</span></div><time>{emailDate(message.sent_at)}</time></header>
              <div className="email-thread-addresses"><span>To</span>{message.recipients || "Not listed"}</div>
              <p>{message.body || "No message text was included."}</p>
            </article>)}
          </div>
        </>}
        {composeSeed && <EmailComposer
          key={composerRevision}
          accounts={accounts}
          canSend={canSend}
          seed={composeSeed}
          onSent={() => {
            setComposeSeed(null);
            setRevision((current) => current + 1);
            setThreadRevision((current) => current + 1);
          }}
        />}
      </section>
    </div>}
  </div>;
}
