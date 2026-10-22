import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { sendGmailEmail } from "../api";
import { createClientId } from "../clientIds";
import type { GmailAttachmentRequest, GmailSendResult, ProviderConnection } from "../types";

const GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send";

export type GmailComposerSeed = {
  recipient?: string;
  subject?: string;
  body?: string;
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
  onSent?: (result: GmailSendResult) => void;
  onClose?: () => void;
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
  onSent,
  onClose,
}: Props) {
  const sendableAccounts = accounts.filter((account) =>
    account.is_active && account.status === "verified" && account.scopes.includes(GMAIL_SEND_SCOPE),
  );
  const [connectionId, setConnectionId] = useState(
    seed?.connectionId ?? sendableAccounts[0]?.id ?? "",
  );
  const [recipient, setRecipient] = useState(seed?.recipient ?? "");
  const [cc, setCc] = useState("");
  const [bcc, setBcc] = useState("");
  const [showCc, setShowCc] = useState(seed?.purpose === "follow_up");
  const [showBcc, setShowBcc] = useState(seed?.purpose === "follow_up");
  const [subject, setSubject] = useState(seed?.subject ?? "");
  const [body, setBody] = useState(seed?.body ?? "");
  const [bodyHtml, setBodyHtml] = useState("");
  const [attachments, setAttachments] = useState<AttachmentDraft[]>([]);
  const [attachmentError, setAttachmentError] = useState<string | null>(null);
  const [encodingAttachments, setEncodingAttachments] = useState(false);
  const [linkOpen, setLinkOpen] = useState(false);
  const [linkUrl, setLinkUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const editorRef = useRef<HTMLDivElement>(null);
  const savedRangeRef = useRef<Range | null>(null);

  useEffect(() => {
    if (!sendableAccounts.some((account) => account.id === connectionId)) {
      setConnectionId(sendableAccounts[0]?.id ?? "");
    }
  }, [accounts, connectionId]);

  useEffect(() => {
    setRecipient(seed?.recipient ?? "");
  }, [seed?.recipient]);

  useEffect(() => {
    const initialBody = seed?.body ?? "";
    setBody(initialBody);
    setBodyHtml("");
    if (editorRef.current) editorRef.current.innerText = initialBody;
  }, [seed?.body]);

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
    if (!canSend || sendBlockedReason || !connectionId || busy) return;
    setBusy(true);
    setNotice(null);
    setError(null);
    try {
      const result = await sendGmailEmail({
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
        ...(leadId ? { lead_id: leadId } : {}),
        ...(seed?.threadId ? { thread_id: seed.threadId } : {}),
        ...(seed?.inReplyTo ? { in_reply_to: seed.inReplyTo } : {}),
      });
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
    }
  }

  function closeComposer() {
    const hasUnsentChanges =
      body.trim() ||
      bodyHtml.trim() ||
      cc.trim() ||
      bcc.trim() ||
      attachments.length > 0 ||
      recipient !== (seed?.recipient ?? "") ||
      subject !== (seed?.subject ?? "");
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
  if (sendableAccounts.length === 0) {
    return <div className="email-composer-empty">
      <span>No verified Gmail account has send permission. Reconnect an account from Integrations to enable sending.</span>
      {onClose && <button type="button" className="button-quiet" onClick={onClose}>Close composer</button>}
    </div>;
  }

  return <form className="email-composer" onSubmit={(event) => void submit(event)}>
    <div className="email-composer-heading">
      <div><span className="eyebrow">{seed?.purpose === "follow_up" ? "Lead follow-up" : seed?.threadId ? "Reply" : "Compose"}</span><h3>{seed?.purpose === "follow_up" ? "Follow-up email" : seed?.threadId ? "Reply to email" : "New email"}</h3></div>
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
    <div className="email-composer-address-grid">
      <label>From
        <select value={connectionId} onChange={(event) => setConnectionId(event.target.value)}>
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
      <input value={subject} onChange={(event) => setSubject(event.target.value)} maxLength={180} required />
    </label>
    <div className="email-rich-editor">
      <span className="email-field-label">Message</span>
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
      <button className="primary-button" type="submit" disabled={busy || Boolean(sendBlockedReason) || encodingAttachments || !connectionId || !recipient.trim() || !subject.trim() || !body.trim()}>
        {busy ? "Sending…" : sendBlockedReason ? "Human review required" : seed?.purpose === "follow_up" ? "Send follow-up" : "Send email"}
      </button>
    </div>
  </form>;
}
