import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { composeEmailWithAI, deleteWorkspaceEmailDraft, saveWorkspaceEmailDraft, sendGmailEmail } from "../api";
import { createClientId } from "../clientIds";
import type { GmailAttachmentRequest, GmailSendRequest, GmailSendResult, ProviderConnection, WorkspaceEmailDraft, WorkspaceEmailDraftInput } from "../types";

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
  const savedRangeRef = useRef<Range | null>(null);

  useEffect(() => {
    if (!replyingInThread && !sendableAccounts.some((account) => account.id === connectionId)) {
      setConnectionId(sendableAccounts[0]?.id ?? "");
    }
  }, [accounts, connectionId, replyingInThread]);

  useEffect(() => {
    if (!draft) setRecipient(seed?.recipient ?? "");
  }, [draft, seed?.recipient]);

  useEffect(() => {
    const initialBody = draft?.data.body ?? seed?.body ?? "";
    setBody(initialBody);
    setBodyHtml(draft?.data.html_body ?? seed?.htmlBody ?? "");
    if (editorRef.current) {
      if (draft?.data.html_body || seed?.htmlBody) editorRef.current.innerHTML = draft?.data.html_body ?? seed?.htmlBody ?? "";
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
      setNotice("Draft saved in Corral. Find it in Email → Drafts.");
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
    try {
      const result = await composeEmailWithAI({
        connection_id: connectionId || null,
        lead_id: leadId ?? draft?.data.lead_id ?? null,
        thread_id: seed?.threadId ?? draft?.data.thread_id ?? null,
        recipient: recipient.trim(),
        subject: subject.trim(),
        current_body: body,
        instruction: aiInstruction.trim() || (replyingInThread
          ? "Read the full email thread and reply to the newest customer message. Address specific questions and proposed meeting dates or times using the conversation context. Keep the reply natural and specific, and do not say a meeting is booked unless the thread confirms it."
          : ""),
      });
      if (!replyingInThread) setSubject(result.subject);
      setBody(result.body);
      setBodyHtml("");
      if (editorRef.current) editorRef.current.innerText = result.body;
      setNotice("AI draft ready. Review the facts and wording before saving or sending.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "AI could not compose this email.");
    } finally {
      setBusy(false);
      setBusyAction(null);
    }
  }

  async function deleteDraft() {
    if (busy || !window.confirm(savedDraft || onDeleteOverride
      ? "Delete this saved draft? This cannot be undone."
      : "Discard this unsent email?")) return;
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
    if (
      hasUnsentChanges &&
      !window.confirm("Close this composer and discard these unsent changes?")
    ) {
      return;
    }
    onClose?.();
  }

  if (!canSend) {
    return <div className="email-composer-empty">You can read this conversation. Sending email requires lead edit permission.</div>;
  }
  return <form className="email-composer" onSubmit={(event) => void submit(event)}>
    <div className="email-composer-heading">
      <div><span className="eyebrow">{headingLabel ?? (seed?.purpose === "follow_up" ? "Lead follow-up" : seed?.threadId ? "Reply" : "Compose")}</span><h3>{headingTitle ?? (seed?.purpose === "follow_up" ? "Follow-up email" : seed?.threadId ? "Reply to email" : "New email")}</h3></div>
      <div className="email-composer-heading-actions">
         <small>Check the recipient and message, then send.</small>
        {onClose && <button
          className="email-composer-close button-quiet"
          type="button"
          aria-label={seed?.threadId ? "Close reply" : "Close email composer"}
          title={seed?.threadId ? "Close reply" : "Close email composer"}
          disabled={busy}
          onClick={closeComposer}
        >
          <span aria-hidden="true">×</span>
        </button>}
      </div>
    </div>
    {sendableAccounts.length === 0 && <div className="notice" role="status">You can save this draft now. Connect a verified Gmail account with send access before sending.</div>}
    <div className="email-composer-address-grid">
      <label>From
        <select value={connectionId} onChange={(event) => setConnectionId(event.target.value)} disabled={replyingInThread}>
          {sendableAccounts.length === 0 && <option value="">Choose a Gmail account before sending</option>}
          {sendableAccounts.map((account) => <option key={account.id} value={account.id}>{account.account_name}</option>)}
        </select>
      </label>
      <div className="email-composer-recipient-fields">
        <label>To
          <input value={recipient} onChange={(event) => setRecipient(event.target.value)} maxLength={1024} autoComplete="email" placeholder="name@example.com" required />
        </label>
        <div className="email-recipient-options">
          <small>Separate multiple addresses with commas.</small>
          <div>
            <button type="button" className="button-quiet" onClick={() => setShowCc((value) => !value)} aria-expanded={showCc}>Cc</button>
            <button type="button" className="button-quiet" onClick={() => setShowBcc((value) => !value)} aria-expanded={showBcc}>Bcc</button>
          </div>
        </div>
        {showCc && <label>Cc<input value={cc} onChange={(event) => setCc(event.target.value)} maxLength={1024} placeholder="copy@example.com" /></label>}
        {showBcc && <label>Bcc<input value={bcc} onChange={(event) => setBcc(event.target.value)} maxLength={1024} placeholder="hidden-copy@example.com" /></label>}
      </div>
    </div>
    <label>Subject
      <input value={subject} onChange={(event) => setSubject(event.target.value)} maxLength={180} required readOnly={replyingInThread} />
    </label>
    {replyingInThread && <small className="email-status">This reply keeps the original Gmail account and subject so it stays in the conversation.</small>}
    {replyUnavailable && <div className="notice" role="alert">Reconnect the Gmail account used for this conversation and refresh its history before replying.</div>}
    <div className="email-rich-editor">
      <span className="email-field-label">Message</span>
      <div className="email-ai-compose">
        <input aria-label="Optional guidance for AI email" value={aiInstruction} onChange={(event) => setAiInstruction(event.target.value)} maxLength={1000} placeholder={replyingInThread ? "Optional: tone or details to emphasize" : "Optional: what should the email focus on?"} disabled={busy} />
        <button className="secondary-button" type="button" onClick={() => void composeWithAI()} disabled={busy || (!recipient.trim() && !leadId && !subject.trim())}>
          {busyAction === "ai" ? "Composing…" : "Compose with AI"}
        </button>
      </div>
      {replyingInThread && <small className="email-status">AI reads this whole thread and lead details automatically. Guidance is optional.</small>}
      <div className="email-format-toolbar" role="toolbar" aria-label="Message formatting">
        <button type="button" aria-label="Bold" title="Bold" onMouseDown={(event) => event.preventDefault()} onClick={() => applyFormat("bold")}><strong>B</strong></button>
        <button type="button" aria-label="Italic" title="Italic" onMouseDown={(event) => event.preventDefault()} onClick={() => applyFormat("italic")}><em>I</em></button>
        <button type="button" aria-label="Underline" title="Underline" onMouseDown={(event) => event.preventDefault()} onClick={() => applyFormat("underline")}><u>U</u></button>
        <button type="button" aria-label="Bulleted list" title="Bulleted list" onMouseDown={(event) => event.preventDefault()} onClick={() => applyFormat("insertUnorderedList")}>• List</button>
        <button type="button" aria-label="Numbered list" title="Numbered list" onMouseDown={(event) => event.preventDefault()} onClick={() => applyFormat("insertOrderedList")}>1. List</button>
        <button type="button" aria-label="Insert link" title="Insert link" onMouseDown={(event) => { event.preventDefault(); saveSelection(); }} onClick={() => { setLinkOpen((value) => !value); setError(null); }}>Link</button>
      </div>
      {linkOpen && <div className="email-link-entry">
        <input type="url" value={linkUrl} onChange={(event) => setLinkUrl(event.target.value)} placeholder="https://example.com" aria-label="Link URL" />
        <button type="button" className="secondary-button" onClick={insertLink} disabled={!linkUrl.trim()}>Insert link</button>
      </div>}
      <div
        ref={editorRef}
        className="email-rich-editor-input"
        contentEditable={!busy}
        role="textbox"
        aria-label="Message"
        aria-multiline="true"
        aria-required="true"
        data-placeholder="Write your message…"
        onInput={syncBody}
        onPaste={() => window.setTimeout(syncBody, 0)}
      />
    </div>
    <label className="email-attachment-picker">Attachments
      <input type="file" multiple disabled={busy || encodingAttachments || attachments.length >= MAX_FILES} onChange={(event) => void addAttachments(event)} />
      <small>Up to {MAX_FILES} files, {MAX_FILE_BYTES / 1024 / 1024} MB each, 10 MB total.</small>
    </label>
    {encodingAttachments && <small role="status">Preparing attachments…</small>}
    {attachmentError && <div className="notice" role="alert">{attachmentError}</div>}
    {attachments.length > 0 && <ul className="email-attachment-list">
      {attachments.map((file) => <li key={file.id}>
        <span>{file.filename} · {(file.size / 1024).toFixed(0)} KB</span>
        <button type="button" className="button-quiet" onClick={() => setAttachments((current) => current.filter((item) => item.id !== file.id))} disabled={busy}>Remove</button>
      </li>)}
    </ul>}
    {error && <div className="notice" role="alert">{error}</div>}
    {notice && <div className="notice" role="status">{notice}</div>}
    {sendBlockedReason && <div className="notice" role="status">{sendBlockedReason}</div>}
    <div className="email-composer-footer">
      <button className="button-quiet" type="button" onClick={() => void deleteDraft()} disabled={busy}>
        {busyAction === "delete" ? "Deleting…" : savedDraft || onDeleteOverride ? "Delete draft" : "Discard"}
      </button>
      <button className="secondary-button" type="button" onClick={() => void saveDraft()} disabled={busy || encodingAttachments || (!recipient.trim() && !subject.trim() && !body.trim())}>
        {busyAction === "save" ? "Saving…" : savedDraft ? "Update draft" : "Save draft"}
      </button>
      <button className="primary-button" type="submit" disabled={busy || Boolean(sendBlockedReason) || replyUnavailable || encodingAttachments || !connectionId || !recipient.trim() || !subject.trim() || !body.trim()}>
        {busyAction === "send" ? "Sending…" : sendBlockedReason ? "Human review required" : sendLabel ?? (seed?.purpose === "follow_up" ? "Send follow-up" : "Send email")}
      </button>
    </div>
  </form>;
}
