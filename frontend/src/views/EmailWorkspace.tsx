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
import { confirmAction } from "../v3/ui/confirm";
import { Icon, type IconName } from "../v3/ui/Icon";
import { Banner, Button, EmptyState, IconButton, Monogram, PageHeader, Sheet } from "../v3/ui/primitives";

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

function listTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const now = new Date();
  if (date.toDateString() === now.toDateString()) return date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  if (date.getFullYear() === now.getFullYear()) return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  return date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function addressesOf(value: string): string[] {
  return value
    .split(",")
    .map((item) => (item.match(/<([^>]+)>/)?.[1] ?? item).trim().toLowerCase())
    .filter((item) => item.includes("@"));
}

const FOLDERS: Array<{ id: "inbox" | "sent" | "drafts" | "spam" | "trash" | "archive"; label: string; icon: IconName }> = [
  { id: "inbox", label: "Inbox", icon: "inbox" },
  { id: "sent", label: "Sent", icon: "send" },
  { id: "drafts", label: "Drafts", icon: "edit" },
  { id: "archive", label: "Archive", icon: "layers" },
  { id: "spam", label: "Spam", icon: "shield" },
  { id: "trash", label: "Trash", icon: "trash" },
];

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
  const [composerMode, setComposerMode] = useState<"sheet" | "reply">("sheet");
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
      setExpandedMessageIds((current) => current.size ? current : new Set(cached.messages.slice(-1).map((message) => message.message_id)));
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
        setExpandedMessageIds((current) => current.size ? current : new Set(result.messages.slice(-1).map((message) => message.message_id)));
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

  const activeMailbox = (accounts.find((account) => account.id === connectionId)?.account_name ?? "").toLowerCase();

  function replySubject(subject: string) {
    return /^re:/i.test(subject) ? subject : `Re: ${subject}`;
  }

  function beginReplyTo(message: GmailThreadMessage, mode: "reply" | "all" = "reply") {
    if (!selected) return;
    setActiveDraft(null);
    const subject = message.subject || selected.subject;
    const outbound = message.direction !== "inbound";
    const primary = outbound ? addressesOf(message.recipients)[0] ?? "" : message.sender;
    const others = mode === "all"
      ? [...addressesOf(message.recipients), message.sender.toLowerCase()].filter((address) => address && address !== activeMailbox && address !== primary.toLowerCase())
      : [];
    setComposeSeed({
      recipient: primary,
      cc: [...new Set(others)].join(", ") || undefined,
      subject: replySubject(subject),
      threadId: message.thread_id || selected.thread_id,
      inReplyTo: message.rfc_message_id || undefined,
      connectionId,
    });
    setComposerMode("reply");
    setComposerRevision((current) => current + 1);
  }

  function beginForward(message: GmailThreadMessage) {
    setActiveDraft(null);
    const subject = message.subject || selected?.subject || "";
    const quoted = [
      "",
      "",
      "---------- Forwarded message ----------",
      `From: ${message.sender_name ? `${message.sender_name} <${message.sender}>` : message.sender}`,
      `Date: ${emailDate(message.sent_at)}`,
      `Subject: ${subject}`,
      `To: ${message.recipients}`,
      "",
      message.body || "",
    ].join("\n");
    setComposeSeed({ subject: /^fwd?:/i.test(subject) ? subject : `Fwd: ${subject}`, body: quoted, connectionId });
    setComposerMode("sheet");
    setComposerRevision((current) => current + 1);
  }

  function latestReplyTarget() {
    return [...thread].reverse().find((message) => message.direction === "inbound") ?? thread[thread.length - 1];
  }

  function beginCompose() {
    setActiveDraft(null);
    setComposeSeed({ connectionId });
    setComposerMode("sheet");
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
  const folderLabel = FOLDERS.find((item) => item.id === folder)?.label ?? "Inbox";
  const unreadCount = folder === "inbox" ? messages.filter((message) => message.unread).length : 0;

  const composer = composeSeed && <EmailComposer
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
  />;

  return <div className="mailbox">
    <PageHeader
      title="Inbox"
      subtitle={activeAccount ? activeAccount.account_name : "Shared Gmail inbox for your workspace"}
      actions={<Button kind="filled" icon="edit" onClick={beginCompose} disabled={!canSend || !accounts.length} title={!canSend ? "Your role can’t send email" : !accounts.length ? "Connect Gmail to send email" : undefined}>New email</Button>}
    />
    {error && <Banner tone="danger" title={error} action={<Button kind="plain" size="sm" onClick={() => setError(null)}>Dismiss</Button>} />}
    {composeSeed && composerMode === "sheet" && (
      <Sheet title={activeDraft ? "Edit draft" : composeSeed.subject?.startsWith("Fwd") ? "Forward" : "New email"} onClose={() => { setComposeSeed(null); setActiveDraft(null); }} width={760}>
        {composer}
      </Sheet>
    )}
    {loadingAccounts && <div className="mailbox-skeleton" role="status" aria-label="Loading mailbox"><i /><i /><i /></div>}
    {!loadingAccounts && accounts.length === 0 && (
      <EmptyState icon="mail" title="Connect Gmail to use the inbox" action={<Button kind="filled" onClick={onOpenIntegrations}>Connect Gmail</Button>}>
        Read, reply and send from your team's Gmail accounts without leaving Corral.
      </EmptyState>
    )}
    {!loadingAccounts && accounts.length > 0 && <div className="mailbox-grid" data-reading={selected ? "" : undefined}>
      <nav className="mailbox-rail" aria-label="Mailbox">
        {accounts.length > 1 && <label className="mailbox-account">
          <span className="v-sr">Gmail account</span>
          <select value={connectionId} onChange={(event) => {
            setConnectionId(event.target.value);
            setPageToken(null);
            setSelected(null);
            setThread([]);
          }}>
            {accounts.map((account) => <option value={account.id} key={account.id}>{account.account_name}</option>)}
          </select>
        </label>}
        <ul>
          {FOLDERS.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                className="mailbox-folder"
                aria-current={folder === item.id ? "page" : undefined}
                onClick={() => { setFolder(item.id); setPageToken(null); setSelected(null); }}
              >
                <Icon name={item.icon} size={16} />
                <span>{item.label}</span>
                {item.id === "inbox" && unreadCount > 0 && <span className="mailbox-count num">{unreadCount}</span>}
                {item.id === "drafts" && drafts.length > 0 && folder === "drafts" && <span className="mailbox-count num">{drafts.length}</span>}
              </button>
            </li>
          ))}
        </ul>
        <Button kind="plain" size="sm" icon="refresh" onClick={() => { backgroundRefresh.current = false; setRevision((current) => current + 1); }} disabled={!connectionId || loadingMessages}>
          {loadingMessages ? "Refreshing…" : "Refresh"}
        </Button>
      </nav>

      <section className="mailbox-list" aria-label={`${folderLabel} messages`}>
        <form className="mailbox-search" onSubmit={submitSearch} role="search">
          <Icon name="search" size={15} />
          <label className="v-sr" htmlFor="shared-email-search">Search {folderLabel}</label>
          <input id="shared-email-search" type="search" value={queryInput} onChange={(event) => setQueryInput(event.target.value)} placeholder={`Search ${folderLabel.toLowerCase()}`} />
        </form>
        {folder === "drafts" && canSend && drafts.length > 0 && <div className="mailbox-drafts" aria-label="Drafts saved in Corral">
          <h3>Saved in Corral</h3>
          {drafts.map((draft) => <div className="mailbox-draft" key={draft.id}>
            <button type="button" onClick={() => {
              getWorkspaceEmailDraft(draft.id).then((full) => {
                setActiveDraft(full);
                setComposeSeed({ connectionId: full.data.connection_id ?? undefined });
                setComposerMode("sheet");
                setComposerRevision((current) => current + 1);
              }).catch((reason) => setError(reason instanceof Error ? reason.message : "Draft could not be opened."));
            }}>
              <b>{draft.subject || "(no subject)"}</b>
              <span>{draft.recipient || "No recipient yet"} · {listTime(draft.updated_at)}</span>
            </button>
            <IconButton icon="trash" size={15} label={`Delete draft ${draft.subject || "without subject"}`} onClick={() => {
              void confirmAction({ title: "Delete this draft?", message: "The saved draft is removed. This can't be undone.", confirmLabel: "Delete draft", tone: "danger" }).then((confirmed) => {
                if (!confirmed) return;
                deleteWorkspaceEmailDraft(draft.id).then(() => setDraftRevision((current) => current + 1)).catch((reason) => setError(reason instanceof Error ? reason.message : "Draft could not be deleted."));
              });
            }} />
          </div>)}
        </div>}
        {loadingMessages && messages.length === 0 && <div className="mailbox-skeleton" role="status" aria-label="Loading messages"><i /><i /><i /><i /></div>}
        {!loadingMessages && messages.length === 0 && <p className="mailbox-empty">{query ? `Nothing in ${folderLabel} matches “${query}”.` : `${folderLabel} is empty.`}</p>}
        <div className="mailbox-rows">
          {messages.map((message) => {
            const name = folder === "sent" ? (message.recipients || "No recipient") : (message.sender_name || message.sender || "Unknown sender");
            return <button
              type="button"
              key={`${message.message_id}-${message.thread_id}`}
              className="mailbox-row"
              aria-current={selected?.message_id === message.message_id ? "true" : undefined}
              data-unread={message.unread || undefined}
              onClick={() => { setSelected(message); setComposeSeed(null); }}
            >
              <Monogram text={name} size={32} />
              <span className="mailbox-row-text">
                <span className="mailbox-row-top">
                  <b>{name}</b>
                  <time dateTime={message.sent_at} title={emailDate(message.sent_at)}>{listTime(message.sent_at)}</time>
                </span>
                <span className="mailbox-row-subject">{message.subject || "(no subject)"}</span>
                <span className="mailbox-row-snippet">{message.snippet}</span>
              </span>
              {message.unread && <span className="mailbox-unread" aria-label="Unread" />}
            </button>;
          })}
        </div>
        {nextPageToken && <Button className="mailbox-more" onClick={() => setPageToken(nextPageToken)} disabled={loadingMessages}>Load more</Button>}
      </section>

      <section className="mailbox-reader" aria-label="Conversation">
        {!selected && <div className="mailbox-reader-empty"><Icon name="mail" size={28} /><p>Select a conversation to read it.</p></div>}
        {selected && <>
          <header className="mailbox-reader-head">
            <IconButton icon="chevronLeft" label="Back to messages" className="mailbox-back" onClick={() => setSelected(null)} />
            <h2>{selected.subject || "(no subject)"}</h2>
            <div className="mailbox-reader-actions">
              {thread.length > 1 && <Button kind="plain" size="sm" onClick={() => setExpandedMessageIds(allThreadMessagesExpanded ? new Set() : new Set(thread.map((message) => message.message_id)))}>
                {allThreadMessagesExpanded ? "Collapse all" : "Expand all"}
              </Button>}
            </div>
          </header>
          {loadingThread && thread.length === 0 && <div className="mailbox-skeleton" role="status" aria-label="Loading conversation"><i /><i /></div>}
          <div className="mailbox-thread">
            {thread.map((message, index) => {
              const expanded = expandedMessageIds.has(message.message_id);
              const body = message.body || "";
              const bodyId = `mail-message-${index}`;
              const who = message.sender_name || message.sender || "Unknown sender";
              return <article className="mailbox-message" data-expanded={expanded || undefined} data-outbound={message.direction !== "inbound" || undefined} key={message.message_id}>
                <button type="button" className="mailbox-message-head" aria-expanded={expanded} aria-controls={bodyId} onClick={() => toggleThreadMessage(message.message_id)}>
                  <Monogram text={who} size={36} />
                  <span className="mailbox-message-who">
                    <b>{who}</b>
                    {expanded ? <small>to {message.recipients || "undisclosed recipients"}</small> : <small className="mailbox-message-preview">{getProviderEmailPreview(body)}</small>}
                  </span>
                  <time dateTime={message.sent_at}>{emailDate(message.sent_at)}</time>
                </button>
                {expanded && <div className="mailbox-message-body" id={bodyId}>
                  <ProviderEmailBody content={body} />
                  {canSend && <div className="mailbox-message-actions">
                    <Button size="sm" icon="reply" onClick={() => beginReplyTo(message, "reply")}>Reply</Button>
                    <Button size="sm" icon="replyAll" onClick={() => beginReplyTo(message, "all")}>Reply all</Button>
                    <Button size="sm" icon="forward" onClick={() => beginForward(message)}>Forward</Button>
                  </div>}
                </div>}
              </article>;
            })}
          </div>
          {composeSeed && composerMode === "reply" ? <div className="mailbox-reply">{composer}</div> : canSend && thread.length > 0 && (
            <div className="mailbox-quick-actions">
              <Button icon="reply" onClick={() => { const target = latestReplyTarget(); if (target) beginReplyTo(target, "reply"); }}>Reply</Button>
              <Button icon="replyAll" onClick={() => { const target = latestReplyTarget(); if (target) beginReplyTo(target, "all"); }}>Reply all</Button>
              <Button icon="forward" onClick={() => { const target = thread[thread.length - 1]; if (target) beginForward(target); }}>Forward</Button>
            </div>
          )}
        </>}
      </section>
    </div>}
  </div>;
}
