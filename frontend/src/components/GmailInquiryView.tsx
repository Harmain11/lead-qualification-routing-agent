import { parseGmailInquiry, type GmailInquiryForward, type GmailInquiryMessage } from "../gmailInquiry";

function formatDate(value: string) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function senderDisplay(value: string) {
  const match = value.match(/^\s*(.*?)\s*<([^<>]+)>\s*$/);
  if (match) {
    return { name: match[1].replace(/^["']|["']$/g, "").trim(), email: match[2].trim() };
  }
  return { name: value.trim(), email: "" };
}

function MessageText({ body }: { body: string }) {
  const text = body.trim();
  const previewLimit = 720;
  if (!text) return <p className="gmail-inquiry-empty">No message text was included.</p>;
  if (text.length <= previewLimit) return <p className="gmail-inquiry-message-body">{text}</p>;
  return <div className="gmail-inquiry-message-body">
    <p>{text.slice(0, previewLimit).trimEnd()}…</p>
    <details><summary>Read full message</summary><pre>{text}</pre></details>
  </div>;
}

function ForwardedMessage({ message }: { message: GmailInquiryForward }) {
  const sender = senderDisplay(message.from);
  return <section className="gmail-inquiry-forward">
    <div className="gmail-inquiry-forward-label"><span aria-hidden="true">↳</span> Forwarded message</div>
    <div className="gmail-inquiry-forward-meta">
      <div><span>From</span><strong>{sender.name || sender.email || "Unknown sender"}</strong>{sender.email && <small>{sender.email}</small>}</div>
      {message.date && <div><span>Date</span><strong>{formatDate(message.date)}</strong></div>}
      {message.subject && <div className="gmail-inquiry-forward-subject"><span>Subject</span><strong>{message.subject}</strong></div>}
      {message.to && <div className="gmail-inquiry-forward-subject"><span>To</span><strong>{message.to}</strong></div>}
    </div>
    <MessageText body={message.body} />
  </section>;
}

function EmailMessage({ message, index }: { message: GmailInquiryMessage; index: number }) {
  const sender = senderDisplay(message.from);
  return <article className="gmail-inquiry-message">
    <header className="gmail-inquiry-message-header">
      <span className="gmail-inquiry-avatar" aria-hidden="true">
        {(sender.name || sender.email || "G").split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toUpperCase()}
      </span>
      <div className="gmail-inquiry-sender">
        <strong>{sender.name || sender.email || "Email sender"}</strong>
        {sender.email && <small>{sender.email}</small>}
      </div>
      <div className="gmail-inquiry-message-meta">
        <span>Message {index + 1}</span>
        {message.date && <time>{formatDate(message.date)}</time>}
      </div>
    </header>
    {message.subject && <h4 className="gmail-inquiry-subject">{message.subject}</h4>}
    {message.to && <p className="gmail-inquiry-recipient"><span>To</span>{message.to}</p>}
    {message.body && <MessageText body={message.body} />}
    {message.forwarded && <ForwardedMessage message={message.forwarded} />}
  </article>;
}

export function GmailInquiryView({ message }: { message: string }) {
  const inquiry = parseGmailInquiry(message);
  if (!inquiry) return <div className="original-inquiry-plain">{message}</div>;
  const role = inquiry.role.replace(/\b\w/g, (letter) => letter.toUpperCase());

  return <div className="gmail-inquiry-view">
    <div className="gmail-inquiry-context">
      <div><span className="eyebrow">Source conversation</span><strong>{inquiry.messages.length} {inquiry.messages.length === 1 ? "email" : "emails"}</strong></div>
      {role && <span className="gmail-inquiry-role">{role}</span>}
      {inquiry.context && <p>{inquiry.context}</p>}
    </div>
    <div className="gmail-inquiry-messages">
      {inquiry.messages.map((item, index) => <EmailMessage key={`${index}-${item.date}-${item.subject}`} message={item} index={index} />)}
    </div>
    <details className="gmail-inquiry-raw">
      <summary>View full original source</summary>
      <pre>{message}</pre>
    </details>
  </div>;
}
