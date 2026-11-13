import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import {
  createCommunicationConversation,
  createCommunicationMessage,
  createCommunicationProject,
  createCommunicationReminder,
  apiUrl,
  getCommunicationBootstrap,
  getCommunicationLinkPreview,
  listCommunicationMessages,
  listCommunicationNotifications,
  listLeads,
  pinCommunicationMessage,
  readCommunicationNotification,
  searchCommunicationMessages,
  unpinCommunicationMessage,
  uploadCommunicationAttachment,
} from "../api";
import type {
  CommunicationBootstrap,
  CommunicationConversation,
  CommunicationMessage,
  CommunicationNotification,
  CommunicationLinkPreview,
  CommunicationAttachment,
  CommunicationProject,
  Lead,
} from "../types";

type CommunicationViewProps = { permissions: ReadonlySet<string>; onOpenLead: (id: string) => void };
type PendingAttachment = { file: File; kind: "file" | "voice"; duration_ms?: number };

const can = (permissions: ReadonlySet<string>, permission: string) => permissions.has(permission);

function conversationLabel(conversation: CommunicationConversation, members: CommunicationBootstrap["members"]) {
  if (conversation.name) return conversation.name;
  return conversation.member_ids
    .map((id) => members.find((member) => member.id === id)?.display_name)
    .filter(Boolean)
    .join(", ") || "Direct message";
}

function time(value: string) {
  return new Date(value).toLocaleString([], { hour: "numeric", minute: "2-digit", month: "short", day: "numeric" });
}

export function CommunicationView({ permissions, onOpenLead }: CommunicationViewProps) {
  const [data, setData] = useState<CommunicationBootstrap | null>(null);
  const [selected, setSelected] = useState<CommunicationConversation | null>(null);
  const [messages, setMessages] = useState<CommunicationMessage[]>([]);
  const [leads, setLeads] = useState<Lead[]>([]);
  const [text, setText] = useState("");
  const [mentionIds, setMentionIds] = useState<string[]>([]);
  const [leadIds, setLeadIds] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState<"project" | "channel" | "dm" | "group" | null>(null);
  const [newMemberIds, setNewMemberIds] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [searchResults, setSearchResults] = useState<CommunicationMessage[]>([]);
  const [notifications, setNotifications] = useState<CommunicationNotification[]>([]);
  const [threadRoot, setThreadRoot] = useState<CommunicationMessage | null>(null);
  const [threadReplies, setThreadReplies] = useState<CommunicationMessage[]>([]);
  const [threadText, setThreadText] = useState("");
  const [pendingAttachments, setPendingAttachments] = useState<PendingAttachment[]>([]);
  const [recording, setRecording] = useState(false);
  const [browserAlertsEnabled, setBrowserAlertsEnabled] = useState(
    typeof Notification !== "undefined" && Notification.permission === "granted",
  );
  const recorderRef = useRef<MediaRecorder | null>(null);
  const recordingStartedAt = useRef(0);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);

  const refresh = async () => {
    setLoading(true);
    try {
      const [next, leadRows, nextNotifications] = await Promise.all([getCommunicationBootstrap(), listLeads({ limit: 50 }), listCommunicationNotifications()]);
      setData(next);
      setLeads(leadRows.items);
      setNotifications(nextNotifications);
      setSelected((current) => current && next.conversations.find((item) => item.id === current.id) || next.conversations[0] || null);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Communication is unavailable.");
    } finally { setLoading(false); }
  };

  useEffect(() => { void refresh(); }, []);

  useEffect(() => {
    const refreshNotifications = () => void listCommunicationNotifications().then((next) => {
      setNotifications((current) => {
        const known = new Set(current.map((notification) => notification.id));
        const newUnread = next.filter((notification) => !notification.read_at && !known.has(notification.id));
        if (browserAlertsEnabled && document.hidden) {
          newUnread.forEach((notification) => new Notification("Workspace communication", { body: `New ${notification.kind.replace("_", " ")} notification` }));
        }
        return next;
      });
    }).catch(() => undefined);
    const interval = window.setInterval(refreshNotifications, 30_000);
    return () => window.clearInterval(interval);
  }, [browserAlertsEnabled]);

  useEffect(() => {
    if (!selected) { setMessages([]); return; }
    let active = true;
    const loadMessages = () => void listCommunicationMessages(selected.id)
      .then((next) => { if (active) setMessages(next); })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "Messages could not be loaded."); });
    loadMessages();
    const interval = window.setInterval(loadMessages, 20_000);
    return () => { active = false; window.clearInterval(interval); };
  }, [selected?.id]);

  useEffect(() => {
    if (!threadRoot) { setThreadReplies([]); return; }
    void listCommunicationMessages(threadRoot.conversation_id, threadRoot.id)
      .then(setThreadReplies)
      .catch((reason) => setError(reason instanceof Error ? reason.message : "Thread replies could not be loaded."));
  }, [threadRoot?.id]);

  const grouped = useMemo(() => (data?.projects ?? []).map((project) => ({ project, conversations: data?.conversations.filter((item) => item.project_id === project.id) ?? [] })), [data]);
  const ungrouped = useMemo(() => data?.conversations.filter((item) => !item.project_id) ?? [], [data]);
  const selectedMentions = data?.members.filter((member) => mentionIds.includes(member.id)) ?? [];
  const selectedLeads = leads.filter((lead) => leadIds.includes(lead.id));

  async function send(event: FormEvent) {
    event.preventDefault();
    if (!selected || (!text.trim() && !pendingAttachments.length)) return;
    try {
      const messageText = text.trim() || (pendingAttachments.some((item) => item.kind === "voice") ? "Voice message" : "Attachment");
      const created = await createCommunicationMessage(selected.id, {
        plaintext: messageText,
        body: { version: 1, type: "markdown", text: messageText },
        mentioned_user_ids: mentionIds,
        lead_ids: leadIds,
      });
      const attachments: CommunicationAttachment[] = [];
      try {
        for (const attachment of pendingAttachments) {
          attachments.push(await uploadCommunicationAttachment(created.id, attachment));
        }
      } catch (reason) {
        setMessages((current) => [...current, { ...created, attachments }]);
        setError(reason instanceof Error ? `${reason.message} The message was sent; retry the attachment.` : "The message was sent, but an attachment could not be uploaded.");
        return;
      }
      setMessages((current) => [...current, { ...created, attachments }]);
      setText(""); setMentionIds([]); setLeadIds([]); setPendingAttachments([]); setError(null);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Message could not be sent."); }
  }

  async function startVoiceRecording() {
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setError("Voice recording is not supported by this browser.");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const chunks: BlobPart[] = [];
      const recorder = new MediaRecorder(stream);
      recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
      recorder.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
        const extension = blob.type.includes("ogg") ? "ogg" : blob.type.includes("mpeg") ? "mp3" : "webm";
        const file = new File([blob], `voice-message.${extension}`, { type: blob.type || "audio/webm" });
        setPendingAttachments((current) => [...current, { file, kind: "voice", duration_ms: Date.now() - recordingStartedAt.current }]);
        setRecording(false);
        recorderRef.current = null;
      };
      recordingStartedAt.current = Date.now();
      recorderRef.current = recorder;
      recorder.start();
      setRecording(true);
      setError(null);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Microphone access is required to record a voice message."); }
  }

  function stopVoiceRecording() { recorderRef.current?.stop(); }

  async function enableBrowserAlerts() {
    if (typeof Notification === "undefined") { setError("Browser alerts are not supported by this browser."); return; }
    const permission = await Notification.requestPermission();
    setBrowserAlertsEnabled(permission === "granted");
    if (permission !== "granted") setError("Browser alerts were not enabled.");
  }

  function formatSelection(prefix: string, suffix = prefix) {
    const field = composerRef.current;
    if (!field) return;
    const selectedText = text.slice(field.selectionStart, field.selectionEnd) || "text";
    const next = `${text.slice(0, field.selectionStart)}${prefix}${selectedText}${suffix}${text.slice(field.selectionEnd)}`;
    setText(next);
    requestAnimationFrame(() => {
      field.focus();
      const start = field.selectionStart + prefix.length;
      field.setSelectionRange(start, start + selectedText.length);
    });
  }

  function formatLink() {
    const url = window.prompt("Link URL (https://…)");
    if (!url?.trim()) return;
    formatSelection("[", `](${url.trim()})`);
  }

  async function create(event: FormEvent) {
    event.preventDefault();
    const form = new FormData(event.currentTarget as HTMLFormElement);
    const name = String(form.get("name") || "").trim();
    const description = String(form.get("description") || "").trim();
    const memberId = String(form.get("member_id") || "");
    const projectId = String(form.get("project_id") || "") || undefined;
    try {
      if (showCreate === "project") await createCommunicationProject({ name, description });
      else {
        await createCommunicationConversation(showCreate === "dm"
          ? { kind: "dm", member_ids: [memberId] }
          : showCreate === "group"
            ? { kind: "group_dm", member_ids: newMemberIds }
            : { kind: "channel", name, description, project_id: projectId, member_ids: newMemberIds });
      }
      setShowCreate(null); setNewMemberIds([]); await refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not create the conversation."); }
  }

  async function pin(messageId: string) {
    try {
      const message = messages.find((item) => item.id === messageId);
      if (message?.is_pinned) {
        await unpinCommunicationMessage(messageId);
        setMessages((current) => current.map((item) => item.id === messageId ? { ...item, is_pinned: false } : item));
      } else {
        const pinned = await pinCommunicationMessage(messageId);
        setMessages((current) => current.map((item) => item.id === pinned.id ? pinned : item));
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not pin the message."); }
  }

  async function remind(messageId: string) {
    const dueAt = new Date(Date.now() + 60 * 60 * 1000).toISOString();
    try { await createCommunicationReminder(messageId, dueAt); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Could not set the reminder."); }
  }

  async function readNotifications() {
    const unread = notifications.filter((item) => !item.read_at);
    try {
      await Promise.all(unread.map((item) => readCommunicationNotification(item.id)));
      setNotifications((current) => current.map((item) => item.read_at ? item : { ...item, read_at: new Date().toISOString() }));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Notifications could not be updated."); }
  }

  async function runSearch(value: string) {
    setSearch(value);
    if (value.trim().length < 2) { setSearchResults([]); return; }
    try { setSearchResults(await searchCommunicationMessages(value)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Search is unavailable."); }
  }

  async function replyInThread(event: FormEvent) {
    event.preventDefault();
    if (!threadRoot || !threadText.trim()) return;
    try {
      const created = await createCommunicationMessage(threadRoot.conversation_id, {
        plaintext: threadText,
        body: { version: 1, type: "plain_text", text: threadText },
        parent_message_id: threadRoot.id,
      });
      setThreadReplies((current) => [...current, created]);
      setThreadText("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Thread reply could not be sent."); }
  }

  if (loading) return <div className="communication-loading" aria-busy="true">Loading your workspace conversations…</div>;
  if (!data) return <div className="communication-loading">{error || "Communication is unavailable."}</div>;

  return <section className="communication-view page-view" aria-label="Workspace communication">
    <header className="communication-header">
      <div><span className="eyebrow">Workspace collaboration</span><h1>Communication</h1><p>Share lead context, coordinate work, and keep decisions with the team.</p></div>
      <div className="communication-header-actions">
        <button className="button-quiet communication-notifications" onClick={() => void readNotifications()} aria-label="Mark communication notifications as read">Alerts{notifications.filter((item) => !item.read_at).length ? ` · ${notifications.filter((item) => !item.read_at).length}` : ""}</button>
        {!browserAlertsEnabled && <button className="button-quiet" onClick={() => void enableBrowserAlerts()}>Enable alerts</button>}
        {can(permissions, "communication.manage") && <button className="secondary-button" onClick={() => setShowCreate("project")}>New project</button>}
        {can(permissions, "communication.create") && <button className="secondary-button" onClick={() => setShowCreate("dm")}>New message</button>}
        {can(permissions, "communication.create") && <button className="secondary-button" onClick={() => setShowCreate("group")}>New group</button>}
        {can(permissions, "communication.create") && <button className="primary-button" onClick={() => setShowCreate("channel")}>New channel</button>}
      </div>
    </header>
    {error && <div className="api-error" role="alert">{error}</div>}
    <div className="communication-layout">
      <aside className="communication-sidebar">
        <label className="communication-search"><span className="sr-only">Search communication</span><input value={search} onChange={(event) => void runSearch(event.target.value)} placeholder="Search messages" /></label>
        {searchResults.length > 0 && <div className="communication-search-results">{searchResults.map((message) => <button key={message.id} onClick={() => { const conversation = data.conversations.find((item) => item.id === message.conversation_id); if (conversation) setSelected(conversation); setSearch(""); setSearchResults([]); }}><b>{message.author.display_name}</b><span>{message.plaintext}</span></button>)}</div>}
        <div className="communication-side-head"><span>Projects</span>{can(permissions, "communication.manage") && <button onClick={() => setShowCreate("project")} aria-label="Create project">+</button>}</div>
        {grouped.map(({ project, conversations }) => <div className="communication-project" key={project.id}><b title={project.description}>{project.name}</b>{project.description && <small>{project.description}</small>}{conversations.map((conversation) => <ConversationButton key={conversation.id} conversation={conversation} active={selected?.id === conversation.id} members={data.members} onClick={() => setSelected(conversation)} />)}</div>)}
        <div className="communication-side-head"><span>Channels & direct messages</span>{can(permissions, "communication.create") && <button onClick={() => setShowCreate("channel")} aria-label="Create channel">+</button>}</div>
        {ungrouped.map((conversation) => <ConversationButton key={conversation.id} conversation={conversation} active={selected?.id === conversation.id} members={data.members} onClick={() => setSelected(conversation)} />)}
      </aside>
      <main className="communication-main">
        {selected ? <>
          <header className="conversation-header"><div><span>{selected.kind === "channel" ? "# Channel" : "Direct message"}</span><h2>{conversationLabel(selected, data.members)}</h2><p>{selected.description || "Private workspace conversation"}</p></div>{selected.primary_lead && <LeadChip lead={selected.primary_lead} onOpen={() => onOpenLead(selected.primary_lead!.id)} />}</header>
          <div className="message-list" aria-live="polite">{messages.length ? messages.map((message) => <article className="message" key={message.id}><div className="message-avatar" aria-hidden="true">{message.author.display_name.slice(0, 1).toUpperCase()}</div><div><header><b>{message.author.display_name}</b><time>{time(message.created_at)}</time>{message.is_pinned && <span className="message-pin">Pinned</span>}<button className="message-pin-button" onClick={() => void pin(message.id)} aria-label={`${message.is_pinned ? "Unpin" : "Pin"} message from ${message.author.display_name}`}>⌖</button><button className="message-pin-button" onClick={() => void remind(message.id)} aria-label={`Remind me about message from ${message.author.display_name}`}>◷</button></header><FormattedMessage text={message.plaintext} /><MessageLinkPreview text={message.plaintext} /><MessageAttachments message={message} />{message.mentioned_user_ids.length > 0 && <div className="message-tags">{message.mentioned_user_ids.map((id) => <span key={id}>@{data.members.find((member) => member.id === id)?.display_name || "member"}</span>)}</div>}{message.leads.map((lead) => <LeadChip key={lead.id} lead={lead} onOpen={() => onOpenLead(lead.id)} />)}<button className="message-thread-button" onClick={() => setThreadRoot(message)}>Reply in thread</button></div></article>) : <div className="conversation-empty">No messages yet. Start the conversation with useful context.</div>}</div>
          {can(permissions, "communication.create") && <form className="message-composer" onSubmit={(event) => void send(event)}>
            <textarea ref={composerRef} value={text} onChange={(event) => setText(event.target.value)} placeholder={`Message ${conversationLabel(selected, data.members)}`} aria-label="Message text" />
            <div className="composer-tools">
              <div className="formatting-tools" aria-label="Text formatting"><button type="button" onClick={() => formatSelection("**")}>B</button><button type="button" onClick={() => formatSelection("_")}>I</button><button type="button" onClick={() => formatSelection("__")}>U</button><button type="button" onClick={() => formatSelection("~~")}>S</button><button type="button" onClick={() => formatSelection("- ", "")}>•</button><button type="button" onClick={() => formatSelection("- [ ] ", "")}>☐</button><button type="button" onClick={formatLink}>↗</button></div>
              <details><summary>@ Mention</summary>{data.members.filter((member) => selected.member_ids.includes(member.id)).map((member) => <label key={member.id}><input type="checkbox" checked={mentionIds.includes(member.id)} onChange={() => setMentionIds((current) => current.includes(member.id) ? current.filter((id) => id !== member.id) : [...current, member.id])} /> {member.display_name}</label>)}</details>
              <details><summary>Link lead</summary>{leads.map((lead) => <label key={lead.id}><input type="checkbox" checked={leadIds.includes(lead.id)} onChange={() => setLeadIds((current) => current.includes(lead.id) ? current.filter((id) => id !== lead.id) : [...current, lead.id])} /> {lead.full_name} · {lead.company_name}</label>)}</details>
              {can(permissions, "communication.attach") && <label className="attachment-picker">Attach<input type="file" multiple onChange={(event) => setPendingAttachments((current) => [...current, ...Array.from(event.target.files || []).map((file) => ({ file, kind: "file" as const }))])} /></label>}
              {can(permissions, "communication.attach") && <button type="button" className="button-quiet" onClick={() => recording ? stopVoiceRecording() : void startVoiceRecording()}>{recording ? "Stop recording" : "Voice message"}</button>}
              <span className="composer-selected">{selectedMentions.map((member) => `@${member.display_name}`).join(" ")} {selectedLeads.map((lead) => lead.full_name).join(" ")} {pendingAttachments.map((attachment) => attachment.file.name).join(" ")}</span>
              <button className="primary-button" disabled={(!text.trim() && !pendingAttachments.length) || recording}>Send</button>
            </div>
          </form>}
        </> : <div className="conversation-empty">Create a channel to start collaborating.</div>}
      </main>
    </div>
    {showCreate && <div className="team-modal-backdrop"><form className="team-modal panel" onSubmit={(event) => void create(event)}><div className="team-modal-head"><div><span className="eyebrow">Communication</span><h2>{showCreate === "project" ? "Create project" : showCreate === "dm" ? "New direct message" : showCreate === "group" ? "New group message" : "Create channel"}</h2><p>{showCreate === "dm" ? "Start a private conversation with one workspace member." : showCreate === "group" ? "Start a private group with at least two teammates." : "Keep work organized with clear names, membership, and descriptions."}</p></div><button type="button" className="modal-close" onClick={() => { setShowCreate(null); setNewMemberIds([]); }} aria-label="Close">×</button></div>{showCreate === "dm" ? <label className="team-field"><span>Member</span><select name="member_id" required defaultValue=""><option value="" disabled>Select a member</option>{data.members.filter((member) => member.id !== data.active_user_id).map((member) => <option key={member.id} value={member.id}>{member.display_name} · {member.email}</option>)}</select></label> : showCreate === "group" ? <MemberPicker members={data.members} activeUserId={data.active_user_id} selectedIds={newMemberIds} onChange={setNewMemberIds} /> : <><label className="team-field"><span>Name</span><input name="name" required /></label><label className="team-field"><span>Description</span><textarea name="description" /></label>{showCreate === "channel" && <><label className="team-field"><span>Project</span><select name="project_id" defaultValue=""><option value="">No project</option>{data.projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label><MemberPicker members={data.members} activeUserId={data.active_user_id} selectedIds={newMemberIds} onChange={setNewMemberIds} /></>}</>}<div className="team-modal-actions"><button type="button" className="button-quiet" onClick={() => { setShowCreate(null); setNewMemberIds([]); }}>Cancel</button><button className="primary-button" disabled={showCreate === "group" && newMemberIds.length < 2}>Create</button></div></form></div>}
    {threadRoot && <div className="team-modal-backdrop"><section className="team-modal panel communication-thread" aria-label="Message thread"><div className="team-modal-head"><div><span className="eyebrow">Thread</span><h2>{threadRoot.author.display_name}</h2><p>{threadRoot.plaintext}</p></div><button type="button" className="modal-close" onClick={() => setThreadRoot(null)} aria-label="Close thread">×</button></div><div className="thread-replies">{threadReplies.length ? threadReplies.map((reply) => <article key={reply.id}><b>{reply.author.display_name}</b><time>{time(reply.created_at)}</time><p>{reply.plaintext}</p></article>) : <p>No replies yet.</p>}</div>{can(permissions, "communication.create") && <form className="message-composer" onSubmit={(event) => void replyInThread(event)}><textarea value={threadText} onChange={(event) => setThreadText(event.target.value)} placeholder="Reply in thread" aria-label="Thread reply" /><div className="composer-tools"><button className="primary-button" disabled={!threadText.trim()}>Reply</button></div></form>}</section></div>}
  </section>;
}

function ConversationButton({ conversation, active, members, onClick }: { conversation: CommunicationConversation; active: boolean; members: CommunicationBootstrap["members"]; onClick: () => void }) {
  return <button className={`conversation-button ${active ? "active" : ""}`} onClick={onClick}><span>{conversation.kind === "channel" ? "#" : "•"}</span>{conversationLabel(conversation, members)}</button>;
}

function LeadChip({ lead, onOpen }: { lead: { full_name: string; company_name: string; qualification: string | null; source: string }; onOpen: () => void }) {
  return <button className="communication-lead-chip" onClick={onOpen}><span>Lead</span><b>{lead.full_name}</b><small>{lead.company_name} · {lead.qualification || lead.source}</small></button>;
}

function MemberPicker({ members, activeUserId, selectedIds, onChange }: { members: CommunicationBootstrap["members"]; activeUserId: string; selectedIds: string[]; onChange: (ids: string[]) => void }) {
  return <fieldset className="communication-member-picker"><legend>Include team members</legend>{members.filter((member) => member.id !== activeUserId).map((member) => <label key={member.id}><input type="checkbox" checked={selectedIds.includes(member.id)} onChange={() => onChange(selectedIds.includes(member.id) ? selectedIds.filter((id) => id !== member.id) : [...selectedIds, member.id])} /> {member.display_name} <small>{member.email}</small></label>)}</fieldset>;
}

function MessageLinkPreview({ text }: { text: string }) {
  const url = text.match(/https?:\/\/[^\s<]+/i)?.[0];
  const [preview, setPreview] = useState<CommunicationLinkPreview | null>(null);
  useEffect(() => { if (!url) return; let active = true; void getCommunicationLinkPreview(url).then((value) => { if (active) setPreview(value); }).catch(() => { if (active) setPreview(null); }); return () => { active = false; }; }, [url]);
  if (!preview) return null;
  return <a className="communication-link-preview" href={preview.url} target="_blank" rel="noreferrer noopener"><span>{preview.domain}</span><b>{preview.title || preview.domain}</b>{preview.description && <small>{preview.description}</small>}</a>;
}

function MessageAttachments({ message }: { message: CommunicationMessage }) {
  if (!message.attachments.length) return null;
  return <div className="message-attachments">{message.attachments.map((attachment) => attachment.kind === "voice"
    ? <audio key={attachment.id} controls preload="metadata" src={apiUrl(attachment.download_url)}>Voice message: {attachment.filename}</audio>
    : <a key={attachment.id} href={apiUrl(attachment.download_url)} target="_blank" rel="noreferrer">Attachment · {attachment.filename}</a>)}</div>;
}

function FormattedMessage({ text }: { text: string }) {
  return <div className="formatted-message">{text.split("\n").map((line, index) => {
    if (line.startsWith("- [ ] ")) return <div className="formatted-todo" key={index}><span aria-hidden="true">☐</span>{formatInline(line.slice(6))}</div>;
    if (line.startsWith("- [x] ")) return <div className="formatted-todo completed" key={index}><span aria-hidden="true">☑</span>{formatInline(line.slice(6))}</div>;
    if (line.startsWith("- ")) return <div className="formatted-bullet" key={index}><span aria-hidden="true">•</span>{formatInline(line.slice(2))}</div>;
    return <p key={index}>{formatInline(line) || " "}</p>;
  })}</div>;
}

function formatInline(value: string) {
  return value.split(/(\*\*[^*]+\*\*|~~[^~]+~~|__[^_]+__|_[^_]+_|\[[^\]]+\]\(https?:\/\/[^)\s]+\))/g).map((token, index) => {
    if (token.startsWith("**") && token.endsWith("**")) return <strong key={index}>{token.slice(2, -2)}</strong>;
    if (token.startsWith("~~") && token.endsWith("~~")) return <s key={index}>{token.slice(2, -2)}</s>;
    if (token.startsWith("__") && token.endsWith("__")) return <u key={index}>{token.slice(2, -2)}</u>;
    if (token.startsWith("_") && token.endsWith("_")) return <em key={index}>{token.slice(1, -1)}</em>;
    const link = token.match(/^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/);
    if (link) return <a key={index} href={link[2]} target="_blank" rel="noreferrer noopener">{link[1]}</a>;
    return token;
  });
}
