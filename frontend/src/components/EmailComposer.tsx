import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { composeEmailWithAI, createLeadMeetingPreferredOfferDraft, deleteWorkspaceEmailDraft, getGoogleCalendarBookingLink, getLeadMeetingRecommendations, saveWorkspaceEmailDraft, sendGmailEmail } from "../api";
import { createClientId } from "../clientIds";
import type { GmailAttachmentRequest, GmailSendRequest, GmailSendResult, MeetingRecommendations, MeetingSuggestion, ProviderConnection, WorkspaceEmailDraft, WorkspaceEmailDraftInput } from "../types";
import { confirmAction } from "../v3/ui/confirm";
import { sanitizeHtml } from "../sanitizeHtml";
import { Icon } from "../v3/ui/Icon";
import { Banner, Button, IconButton, Menu, MenuItem } from "../v3/ui/primitives";

const GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send";

export type GmailComposerSeed = {
  recipient?: string;
  subject?: string;
  body?: string;
  htmlBody?: string;
  cc?: string;
  bcc?: string;
  attachments?: GmailAttachmentRequest[];
  purpose?: "follow_up";
  threadId?: string;
  inReplyTo?: string;
  connectionId?: string;
};

type Props = {
  accounts: ProviderConnection[];
  canSend: boolean;
  sendBlockedReason?: string;
  leadId?: string;
  seed?: GmailComposerSeed;
  draft?: WorkspaceEmailDraft;
  onSent?: (result: GmailSendResult) => void;
  onSaved?: (draft: WorkspaceEmailDraft) => void;
  onDeleted?: () => void;
  onClose?: () => void;
  onSaveOverride?: (payload: WorkspaceEmailDraftInput) => Promise<void>;
  onSendOverride?: (payload: GmailSendRequest) => Promise<GmailSendResult>;
  onDeleteOverride?: () => Promise<void>;
  sendLabel?: string;
  headingLabel?: string;
  headingTitle?: string;
};

type AttachmentDraft = GmailAttachmentRequest & {
  id: string;
  size: number;
};

const MAX_FILES = 3;
const MAX_FILE_BYTES = 5 * 1024 * 1024;
const MAX_TOTAL_BYTES = 10 * 1024 * 1024;

function encodeBase64(bytes: Uint8Array) {
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 32_768) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 32_768));
  }
  return btoa(binary);
}

export function EmailComposer({
  accounts,
  canSend,
  sendBlockedReason,
  leadId,
  seed,
  draft,
  onSent,
  onSaved,
  onDeleted,
  onClose,
  onSaveOverride,
  onSendOverride,
  onDeleteOverride,
  sendLabel,
  headingLabel,
  headingTitle,
}: Props) {
  const sendableAccounts = accounts.filter((account) =>
    account.is_active && account.status === "verified" && account.scopes.includes(GMAIL_SEND_SCOPE),
  );
  const [connectionId, setConnectionId] = useState(
    draft?.data.connection_id ?? seed?.connectionId ?? sendableAccounts[0]?.id ?? "",
  );
  const [recipient, setRecipient] = useState(draft?.data.recipient ?? seed?.recipient ?? "");
  const [cc, setCc] = useState(draft?.data.cc ?? seed?.cc ?? "");
  const [bcc, setBcc] = useState(draft?.data.bcc ?? seed?.bcc ?? "");
  const [showCc, setShowCc] = useState(seed?.purpose === "follow_up" || Boolean(seed?.cc));
  const [showBcc, setShowBcc] = useState(seed?.purpose === "follow_up" || Boolean(seed?.bcc));
  const [subject, setSubject] = useState(draft?.data.subject ?? seed?.subject ?? "");
  const [body, setBody] = useState(draft?.data.body ?? seed?.body ?? "");
  const [bodyHtml, setBodyHtml] = useState(draft?.data.html_body ?? seed?.htmlBody ?? "");
  const [attachments, setAttachments] = useState<AttachmentDraft[]>(() =>
    (draft?.data.attachments ?? seed?.attachments ?? []).map((file) => ({ ...file, id: createClientId(), size: Math.floor(file.content_base64.length * 3 / 4) })),
  );
  const [attachmentError, setAttachmentError] = useState<string | null>(null);
  const [encodingAttachments, setEncodingAttachments] = useState(false);
  const [linkOpen, setLinkOpen] = useState(false);
  const [linkUrl, setLinkUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [busyAction, setBusyAction] = useState<"ai" | "save" | "send" | "delete" | null>(null);
  const [aiInstruction, setAiInstruction] = useState("");
  const [aiOpen, setAiOpen] = useState(false);
  const [beforeAi, setBeforeAi] = useState<{ subject: string; body: string; html: string } | null>(null);
  const [proposeOpen, setProposeOpen] = useState(false);
  const [proposals, setProposals] = useState<string[]>(["", "", ""]);
  const [meetingRecommendations, setMeetingRecommendations] = useState<MeetingRecommendations | null>(null);
  const [meetingLoading, setMeetingLoading] = useState(false);
  const [meetingDraftBusy, setMeetingDraftBusy] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [savedDraft, setSavedDraft] = useState(draft ?? null);
  const replyingInThread = Boolean(seed?.threadId || draft?.data.thread_id);
  const replyConnectionId = seed?.threadId ? seed.connectionId : draft?.data.connection_id;
  const replyReference = seed?.threadId ? seed.inReplyTo : draft?.data.in_reply_to;
  const replyUnavailable = replyingInThread && (
    !replyConnectionId || !replyReference || connectionId !== replyConnectionId
    || !sendableAccounts.some((account) => account.id === replyConnectionId)
  );
  const savedPayload = useRef<string | null>(draft ? JSON.stringify(draft.data) : null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const editorRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLFormElement>(null);
  const savedRangeRef = useRef<Range | null>(null);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      composerRef.current?.scrollIntoView({
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
        block: "start",
      });
    });
    return () => window.cancelAnimationFrame(frame);
  }, []);

  useEffect(() => {
    if (!replyingInThread && !sendableAccounts.some((account) => account.id === connectionId)) {
      setConnectionId(sendableAccounts[0]?.id ?? "");
    }
  }, [accounts, connectionId, replyingInThread]);

  useEffect(() => {
    if (!leadId || !replyingInThread) {
      setMeetingRecommendations(null);
      return;
    }
    const controller = new AbortController();
    setMeetingLoading(true);
    void getLeadMeetingRecommendations(leadId, controller.signal)
      .then((recommendations) => {
        if (!controller.signal.aborted) setMeetingRecommendations(recommendations);
      })
      .catch(() => {
        if (!controller.signal.aborted) setMeetingRecommendations(null);
      })
      .finally(() => {
        if (!controller.signal.aborted) setMeetingLoading(false);
      });
    return () => controller.abort();
  }, [leadId, replyingInThread]);

  useEffect(() => {
    if (!draft) setRecipient(seed?.recipient ?? "");
  }, [draft, seed?.recipient]);

  useEffect(() => {
    const initialBody = draft?.data.body ?? seed?.body ?? "";
    setBody(initialBody);
    setBodyHtml(draft?.data.html_body ?? seed?.htmlBody ?? "");
    if (editorRef.current) {
      if (draft?.data.html_body || seed?.htmlBody) editorRef.current.innerHTML = sanitizeHtml(draft?.data.html_body ?? seed?.htmlBody ?? "");
      else editorRef.current.innerText = initialBody;
    }
  }, [draft, seed?.body, seed?.htmlBody]);

  function currentDraftPayload(): WorkspaceEmailDraftInput {
    return {
      connection_id: connectionId || null,
      recipient: recipient.trim(), cc: cc.trim(), bcc: bcc.trim(),
      subject: subject.trim(), body, html_body: bodyHtml || null,
      attachments: attachments.map(({ id: _id, size: _size, ...file }) => file),
      lead_id: leadId ?? draft?.data.lead_id ?? null,
      thread_id: seed?.threadId ?? draft?.data.thread_id ?? null,
      in_reply_to: seed?.inReplyTo ?? draft?.data.in_reply_to ?? null,
    };
  }

  useEffect(() => {
    if (draft) savedPayload.current = JSON.stringify(currentDraftPayload());
  }, [draft]);

  async function saveDraft() {
    if (!canSend || busy || encodingAttachments) return;
    const payload = currentDraftPayload();
    setBusy(true);
    setBusyAction("save");
    setError(null);
    setNotice(null);
    try {
      if (onSaveOverride) {
        await onSaveOverride(payload);
        savedPayload.current = JSON.stringify(payload);
        setNotice("Appointment draft saved. It has not been sent.");
        return;
      }
      const result = await saveWorkspaceEmailDraft(
        { ...payload, ...(savedDraft ? { expected_version: savedDraft.version } : {}) },
        savedDraft?.id,
      );
      setSavedDraft(result);
      savedPayload.current = JSON.stringify(payload);
      setNotice("Draft saved in Corral. Find it in Email › Drafts.");
      onSaved?.(result);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Draft could not be saved.");
    } finally {
      setBusy(false);
      setBusyAction(null);
    }
  }

  async function composeWithAI() {
    if (busy) return;
    setBusy(true);
    setBusyAction("ai");
    setError(null);
    setNotice(null);
    const snapshot = { subject, body, html: editorRef.current?.innerHTML ?? bodyHtml };
    try {
      const result = await composeEmailWithAI({
        connection_id: connectionId || null,
        lead_id: leadId ?? draft?.data.lead_id ?? null,
        thread_id: seed?.threadId ?? draft?.data.thread_id ?? null,
        in_reply_to: seed?.inReplyTo ?? draft?.data.in_reply_to ?? null,
        recipient: recipient.trim(),
        subject: subject.trim(),
        current_body: body,
        instruction: aiInstruction.trim() || (replyingInThread
          ? "Read the full email thread and reply directly to the selected message (or the newest customer message when none is selected). Identify what the sender is asking, explaining, or deciding, then answer that point with relevant details from the thread. Acknowledge notices or concerns without copying or summarizing the whole email back to them. Do not force a meeting invitation when it does not fit the sender's message; when a meeting is relevant, offer one clear next step. Never claim a meeting or other action is completed unless the thread confirms it."
          : ""),
      });
      if (!replyingInThread) setSubject(result.subject);
      setBody(result.body);
      setBodyHtml("");
      if (editorRef.current) editorRef.current.innerText = result.body;
      setBeforeAi(snapshot);
      setAiOpen(false);
      setNotice("AI draft ready. Check the facts and wording before you send.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "AI could not compose this email.");
    } finally {
      setBusy(false);
      setBusyAction(null);
    }
  }

  async function deleteDraft() {
    if (busy) return;
    const saved = Boolean(savedDraft || onDeleteOverride);
    if (!(await confirmAction(saved
      ? { title: "Delete this draft?", message: "The saved draft is removed. This can't be undone.", confirmLabel: "Delete draft", tone: "danger" }
      : { title: "Discard this email?", message: "Your unsent changes will be lost.", confirmLabel: "Discard", tone: "danger" }))) return;
    setBusy(true);
    setBusyAction("delete");
    setError(null);
    try {
      if (onDeleteOverride) await onDeleteOverride();
      else if (savedDraft) await deleteWorkspaceEmailDraft(savedDraft.id);
      setSavedDraft(null);
      setBody("");
      setBodyHtml("");
      if (editorRef.current) editorRef.current.innerHTML = "";
      onDeleted?.();
      onClose?.();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Draft could not be deleted.");
    } finally {
      setBusy(false);
      setBusyAction(null);
    }
  }

  function syncBody() {
    const editor = editorRef.current;
    if (!editor) return;
    setBody(editor.innerText.replace(/\u00a0/g, " "));
    setBodyHtml(editor.innerHTML);
  }

  function applyFormat(command: "bold" | "italic" | "underline" | "insertUnorderedList" | "insertOrderedList") {
    editorRef.current?.focus();
    document.execCommand(command, false);
    syncBody();
  }

  function saveSelection() {
    const selection = window.getSelection();
    if (!selection?.rangeCount || !editorRef.current?.contains(selection.anchorNode)) return;
    savedRangeRef.current = selection.getRangeAt(0).cloneRange();
  }

  function insertLink() {
    const editor = editorRef.current;
    if (!editor) return;
    let url: URL;
    try {
      url = new URL(linkUrl.trim());
    } catch {
      setError("Enter a complete link, such as https://example.com.");
      return;
    }
    if (!["http:", "https:", "mailto:"].includes(url.protocol)) {
      setError("Links must use https, http, or mailto.");
      return;
    }
    editor.focus();
    const selection = window.getSelection();
    if (selection && savedRangeRef.current) {
      selection.removeAllRanges();
      selection.addRange(savedRangeRef.current);
    }
    document.execCommand("createLink", false, url.toString());
    syncBody();
    setLinkOpen(false);
    setLinkUrl("");
    savedRangeRef.current = null;
    setError(null);
  }

  async function addAttachments(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.currentTarget.files ?? []);
    event.currentTarget.value = "";
    if (!files.length) return;
    const count = attachments.length + files.length;
    const total = attachments.reduce((sum, file) => sum + file.size, 0)
      + files.reduce((sum, file) => sum + file.size, 0);
    if (count > MAX_FILES) {
      setAttachmentError(`Choose up to ${MAX_FILES} files.`);
      return;
    }
    if (files.some((file) => file.size > MAX_FILE_BYTES)) {
      setAttachmentError("Each attachment must be 5 MB or smaller.");
      return;
    }
    if (total > MAX_TOTAL_BYTES) {
      setAttachmentError("Attachments must total 10 MB or less.");
      return;
    }
    setEncodingAttachments(true);
    setAttachmentError(null);
    try {
      const encoded = await Promise.all(files.map(async (file) => ({
        id: createClientId(),
        filename: file.name,
        content_type: file.type || "application/octet-stream",
        content_base64: encodeBase64(new Uint8Array(await file.arrayBuffer())),
        size: file.size,
      })));
      setAttachments((current) => [...current, ...encoded]);
    } catch {
      setAttachmentError("One or more files could not be attached.");
    } finally {
      setEncodingAttachments(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!canSend || sendBlockedReason || replyUnavailable || !connectionId || busy) return;
    setBusy(true);
    setBusyAction("send");
    setNotice(null);
    setError(null);
    try {
      const request: GmailSendRequest = {
        connection_id: connectionId,
        recipient: recipient.trim(),
        ...(cc.trim() ? { cc: cc.trim() } : {}),
        ...(bcc.trim() ? { bcc: bcc.trim() } : {}),
        subject: subject.trim(),
        body,
        ...(bodyHtml.trim() ? { html_body: bodyHtml } : {}),
        ...(attachments.length ? {
          attachments: attachments.map(({ id: _id, size: _size, ...file }) => file),
        } : {}),
        ...(leadId || draft?.data.lead_id ? { lead_id: leadId ?? draft?.data.lead_id ?? undefined } : {}),
        ...(seed?.threadId || draft?.data.thread_id ? { thread_id: seed?.threadId ?? draft?.data.thread_id ?? undefined } : {}),
        ...(seed?.inReplyTo || draft?.data.in_reply_to ? { in_reply_to: seed?.inReplyTo ?? draft?.data.in_reply_to ?? undefined } : {}),
        ...(savedDraft ? { draft_id: savedDraft.id } : {}),
      };
      const result = onSendOverride
        ? await onSendOverride(request)
        : await sendGmailEmail(request);
      const account = sendableAccounts.find((item) => item.id === connectionId);
      setNotice(`Email sent from ${account?.account_name ?? result.sender}.`);
      setBody("");
      setBodyHtml("");
      setCc("");
      setBcc("");
      setShowCc(false);
      setShowBcc(false);
      setAttachments([]);
      if (editorRef.current) editorRef.current.innerHTML = "";
      onSent?.(result);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Email could not be sent.");
    } finally {
      setBusy(false);
      setBusyAction(null);
    }
  }

  function closeComposer() {
    const hasUnsentChanges = savedPayload.current
      ? JSON.stringify(currentDraftPayload()) !== savedPayload.current
      : Boolean(
      body.trim() ||
      bodyHtml.trim() ||
      cc.trim() ||
      bcc.trim() ||
      attachments.length > 0 ||
      recipient !== (seed?.recipient ?? "") ||
      subject !== (seed?.subject ?? ""));
    if (hasUnsentChanges) {
      void confirmAction({ title: "Discard unsent changes?", message: "Closing the composer loses what you've written.", confirmLabel: "Discard", cancelLabel: "Keep editing", tone: "danger" })
        .then((confirmed) => { if (confirmed) onClose?.(); });
      return;
    }
    onClose?.();
  }

  function undoAi() {
    if (!beforeAi) return;
    if (!replyingInThread) setSubject(beforeAi.subject);
    setBody(beforeAi.body);
    setBodyHtml(beforeAi.html);
    if (editorRef.current) editorRef.current.innerHTML = sanitizeHtml(beforeAi.html) || "";
    if (editorRef.current && !beforeAi.html) editorRef.current.innerText = beforeAi.body;
    setBeforeAi(null);
    setNotice("Restored your previous text.");
  }

  function insertText(text: string) {
    const editor = editorRef.current;
    if (!editor) return;
    editor.focus();
    const selection = window.getSelection();
    if (savedRangeRef.current && selection) {
      selection.removeAllRanges();
      selection.addRange(savedRangeRef.current);
    } else if (selection) {
      selection.selectAllChildren(editor);
      selection.collapseToEnd();
    }
    document.execCommand("insertText", false, text);
    savedRangeRef.current = null;
    syncBody();
  }

  async function insertBookingLink() {
    try {
      const link = await getGoogleCalendarBookingLink();
      if (!link.url) {
        setError("No booking link is set. Add your Google Calendar booking page in Settings › Email automation.");
        return;
      }
      insertText(`\nYou can choose a time that suits you here: ${link.url}\n`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The booking link couldn't be loaded.");
    }
  }

  function insertProposedTimes() {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const times = proposals
      .map((value) => new Date(value))
      .filter((date) => !Number.isNaN(date.getTime()))
      .map((date, index) => `${index + 1}. ${date.toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" })}`);
    if (!times.length) {
      setError("Choose at least one date and time to propose.");
      return;
    }
    insertText(`\nWould any of these times work for you? (${zone})\n${times.join("\n")}\nReply with the number that suits you, or suggest another time.\n`);
    setProposeOpen(false);
    setProposals(["", "", ""]);
    setError(null);
  }

  function meetingTime(slot: MeetingSuggestion) {
    const date = new Date(slot.start_at);
    return Number.isNaN(date.getTime())
      ? slot.start_at
      : date.toLocaleString(undefined, {
        weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short",
      });
  }

  async function prepareAppointmentDraft() {
    if (!leadId || meetingDraftBusy) return;
    setMeetingDraftBusy(true);
    setError(null);
    try {
      await createLeadMeetingPreferredOfferDraft(leadId);
      setNotice("AI appointment draft prepared from this lead’s thread and current calendar availability. Open Drafts to review it before sending.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The appointment draft could not be prepared.");
    } finally {
      setMeetingDraftBusy(false);
    }
  }

  const canSubmit = !busy && !sendBlockedReason && !replyUnavailable && !encodingAttachments && Boolean(connectionId)
    && Boolean(recipient.trim()) && Boolean(subject.trim()) && Boolean(body.trim());

  if (!canSend) {
    return <p className="mail-readonly">You can read this conversation. Sending email needs lead edit access.</p>;
  }
  const heading = headingTitle ?? (seed?.purpose === "follow_up" ? "Follow-up email" : seed?.threadId ? "Reply" : "New email");
  return <form
    ref={composerRef}
    className="mail-composer"
    aria-label={heading}
    onSubmit={(event) => void submit(event)}
    onKeyDown={(event) => {
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter" && canSubmit) {
        event.preventDefault();
        composerRef.current?.requestSubmit();
      }
    }}
  >
    <header className="mail-composer-head">
      <div>
        {headingLabel && <span className="mail-composer-eyebrow">{headingLabel}</span>}
        <h3>{heading}</h3>
      </div>
      {onClose && <IconButton icon="close" label={seed?.threadId ? "Close reply" : "Close composer"} size={16} disabled={busy} onClick={closeComposer} />}
    </header>
    {sendableAccounts.length === 0 && <Banner tone="info" title="Connect Gmail to send">You can save this as a draft now. Sending needs a verified Gmail account with send access.</Banner>}
    <div className="mail-fields">
      <label className="mail-field">
        <span>From</span>
        <select value={connectionId} onChange={(event) => setConnectionId(event.target.value)} disabled={replyingInThread}>
          {sendableAccounts.length === 0 && <option value="">No sending account</option>}
          {sendableAccounts.map((account) => <option key={account.id} value={account.id}>{account.account_name}</option>)}
        </select>
      </label>
      <div className="mail-field">
        <span>To</span>
        <input value={recipient} onChange={(event) => setRecipient(event.target.value)} maxLength={1024} autoComplete="email" placeholder="name@company.com" required aria-label="To" />
        <span className="mail-field-toggles">
          {!showCc && <button type="button" onClick={() => setShowCc(true)}>Cc</button>}
          {!showBcc && <button type="button" onClick={() => setShowBcc(true)}>Bcc</button>}
        </span>
      </div>
      {showCc && <label className="mail-field"><span>Cc</span><input value={cc} onChange={(event) => setCc(event.target.value)} maxLength={1024} placeholder="Separate addresses with commas" /></label>}
      {showBcc && <label className="mail-field"><span>Bcc</span><input value={bcc} onChange={(event) => setBcc(event.target.value)} maxLength={1024} placeholder="Separate addresses with commas" /></label>}
      <label className="mail-field">
        <span>Subject</span>
        <input value={subject} onChange={(event) => setSubject(event.target.value)} maxLength={180} required readOnly={replyingInThread} title={replyingInThread ? "Replies keep the original subject so they stay in the conversation." : undefined} />
      </label>
    </div>
    {replyUnavailable && <Banner tone="danger" title="This reply can't be sent yet">Reconnect the Gmail account used for this conversation, then refresh the thread.</Banner>}
    <div className="mail-toolbar" role="toolbar" aria-label="Formatting and tools">
      <IconButton icon="bold" label="Bold" size={16} onMouseDown={(event) => event.preventDefault()} onClick={() => applyFormat("bold")} />
      <IconButton icon="italic" label="Italic" size={16} onMouseDown={(event) => event.preventDefault()} onClick={() => applyFormat("italic")} />
      <IconButton icon="underline" label="Underline" size={16} onMouseDown={(event) => event.preventDefault()} onClick={() => applyFormat("underline")} />
      <span className="mail-toolbar-sep" aria-hidden="true" />
      <IconButton icon="listBullets" label="Bulleted list" size={16} onMouseDown={(event) => event.preventDefault()} onClick={() => applyFormat("insertUnorderedList")} />
      <IconButton icon="listNumbers" label="Numbered list" size={16} onMouseDown={(event) => event.preventDefault()} onClick={() => applyFormat("insertOrderedList")} />
      <IconButton icon="link" label="Insert link" size={16} onMouseDown={(event) => { event.preventDefault(); saveSelection(); }} onClick={() => { setLinkOpen((value) => !value); setError(null); }} />
      <span className="mail-toolbar-sep" aria-hidden="true" />
      <IconButton icon="paperclip" label="Attach files" size={16} disabled={busy || encodingAttachments || attachments.length >= MAX_FILES} onClick={() => fileInputRef.current?.click()} />
      <Menu label="Scheduling" iconOnly="calendar" trigger={null}>
        {(close) => (
          <>
            {leadId && <MenuItem icon="calendar" onSelect={() => { close(); saveSelection(); void prepareAppointmentDraft(); }}>Create AI appointment draft</MenuItem>}
            <MenuItem icon="link" onSelect={() => { close(); saveSelection(); void insertBookingLink(); }}>Insert booking link</MenuItem>
            <MenuItem icon="clock" onSelect={() => { close(); saveSelection(); setProposeOpen(true); }}>Propose times…</MenuItem>
          </>
        )}
      </Menu>
      <span className="mail-toolbar-spacer" />
      <Button kind={aiOpen ? "tinted" : "plain"} size="sm" icon="sparkle" aria-expanded={aiOpen} onClick={() => setAiOpen((value) => !value)} disabled={busy}>
        {busyAction === "ai" ? "Writing…" : "Compose with AI"}
      </Button>
    </div>
    <input ref={fileInputRef} type="file" multiple hidden onChange={(event) => void addAttachments(event)} />
    {aiOpen && <div className="mail-ai">
      <label className="mail-ai-label" htmlFor="mail-ai-instruction">
        {replyingInThread ? "Corral reads the whole thread and the lead record." : "Tell Corral what the email should say."} Add instructions if you like.
      </label>
      <textarea
        id="mail-ai-instruction"
        rows={2}
        value={aiInstruction}
        onChange={(event) => setAiInstruction(event.target.value)}
        maxLength={1000}
        placeholder={replyingInThread ? "e.g. Answer their pricing question and offer a call next week" : "e.g. Follow up on the proposal, friendly and brief"}
        disabled={busy}
      />
      <div className="mail-ai-actions">
        <Button kind="plain" size="sm" onClick={() => setAiOpen(false)}>Cancel</Button>
        <Button kind="filled" size="sm" icon="sparkle" disabled={busy || (!recipient.trim() && !leadId && !subject.trim() && !replyingInThread)} onClick={() => void composeWithAI()}>
          {busyAction === "ai" ? "Writing…" : body.trim() ? "Rewrite draft" : "Write draft"}
        </Button>
      </div>
    </div>}
    {linkOpen && <div className="mail-inline-panel">
      <input type="url" value={linkUrl} onChange={(event) => setLinkUrl(event.target.value)} placeholder="https://example.com" aria-label="Link address" autoFocus />
      <Button size="sm" onClick={() => setLinkOpen(false)}>Cancel</Button>
      <Button kind="filled" size="sm" onClick={insertLink} disabled={!linkUrl.trim()}>Insert</Button>
    </div>}
    {proposeOpen && <div className="mail-inline-panel mail-propose" role="group" aria-label="Propose meeting times">
      <span className="mail-propose-label">Propose up to three times. Nothing is booked until they confirm.</span>
      {proposals.map((value, index) => (
        <input key={index} type="datetime-local" value={value} aria-label={`Option ${index + 1}`} onChange={(event) => setProposals((current) => current.map((item, i) => (i === index ? event.target.value : item)))} />
      ))}
      <Button size="sm" onClick={() => setProposeOpen(false)}>Cancel</Button>
      <Button kind="filled" size="sm" onClick={insertProposedTimes}>Insert times</Button>
    </div>}
    {leadId && replyingInThread && (meetingLoading || meetingRecommendations?.detected) && <div className="mail-inline-panel mail-propose" role="status" aria-label="Meeting availability">
      {meetingLoading ? <span>Checking the lead’s connected calendar…</span> : <>
        <span className="mail-propose-label">Meeting request detected · {meetingRecommendations!.suggestions.length > 0 ? `${meetingRecommendations!.suggestions.length} verified time option${meetingRecommendations!.suggestions.length === 1 ? "" : "s"} available` : "the appointment draft will use the connected booking link"}</span>
        {meetingRecommendations!.suggestions.slice(0, 5).map((slot) => <span key={`${slot.provider}:${slot.start_at}`} className="mail-propose-label">{slot.provider === "calendly" ? "Calendly" : "Google Calendar"} · {meetingTime(slot)}</span>)}
        <Button kind="filled" size="sm" icon="calendar" onClick={() => void prepareAppointmentDraft()} disabled={meetingDraftBusy}>
          {meetingDraftBusy ? "Preparing…" : "Create AI appointment draft"}
        </Button>
      </>}
    </div>}
    <div
      ref={editorRef}
      className="mail-editor"
      contentEditable={!busy}
      role="textbox"
      aria-label="Message"
      aria-multiline="true"
      aria-required="true"
      data-placeholder="Write your message…"
      onInput={syncBody}
      onBlur={saveSelection}
      onPaste={() => window.setTimeout(syncBody, 0)}
    />
    {(encodingAttachments || attachments.length > 0) && <ul className="mail-attachments" aria-label="Attachments">
      {attachments.map((file) => <li key={file.id}>
        <Icon name="paperclip" size={14} />
        <span>{file.filename}</span>
        <small>{(file.size / 1024).toFixed(0)} KB</small>
        <IconButton icon="close" size={12} label={`Remove ${file.filename}`} disabled={busy} onClick={() => setAttachments((current) => current.filter((item) => item.id !== file.id))} />
      </li>)}
      {encodingAttachments && <li role="status">Preparing attachments…</li>}
    </ul>}
    {attachmentError && <Banner tone="danger" title={attachmentError} />}
    {error && <Banner tone="danger" title={error} />}
    {notice && <Banner title={notice} action={beforeAi ? <Button kind="plain" size="sm" onClick={undoAi}>Undo AI draft</Button> : undefined} />}
    {sendBlockedReason && <Banner tone="attention" title="Sending is on hold">{sendBlockedReason}</Banner>}
    <footer className="mail-composer-foot">
      <Button kind="plain" onClick={() => void deleteDraft()} disabled={busy} icon="trash">
        {busyAction === "delete" ? "Deleting…" : savedDraft || onDeleteOverride ? "Delete draft" : "Discard"}
      </Button>
      <span className="mail-toolbar-spacer" />
      <small className="mail-shortcut" aria-hidden="true">⌘↵ to send</small>
      <Button onClick={() => void saveDraft()} disabled={busy || encodingAttachments || (!recipient.trim() && !subject.trim() && !body.trim())}>
        {busyAction === "save" ? "Saving…" : savedDraft ? "Update draft" : "Save draft"}
      </Button>
      <Button kind="filled" type="submit" icon="send" disabled={!canSubmit} title={sendBlockedReason ? `On hold: ${sendBlockedReason}` : undefined}>
        {busyAction === "send" ? "Sending…" : sendLabel ?? (seed?.purpose === "follow_up" ? "Send follow-up" : "Send")}
      </Button>
    </footer>
  </form>;
}
