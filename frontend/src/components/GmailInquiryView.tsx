import { Fragment, type ReactNode } from "react";

import {
  cleanGmailInquiryText,
  parseEmailDate,
  parseGmailInquiry,
  splitGmailBodyForDisplay,
  type GmailBodyDisplaySections,
  type GmailInquiryForward,
  type GmailInquiryMessage,
} from "../gmailInquiry";

type PreparedMessage = {
  message: GmailInquiryForward;
  body: GmailBodyDisplaySections;
  forwarded: PreparedMessage | null;
};

function formatDate(value: string) {
  if (!value) return "";
  const date = parseEmailDate(value);
  return !date
    ? value
    : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function dateTimeValue(value: string) {
  const date = parseEmailDate(value);
  return date ? date.toISOString() : undefined;
}

function senderDisplay(value: string) {
  const match = value.match(/^\s*(.*?)\s*<([^<>]+)>\s*$/);
  if (match) {
    return { name: match[1].replace(/^["']|["']$/g, "").trim(), email: match[2].trim() };
  }
  const email = value.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i)?.[0] || "";
  return { name: value.replace(email, "").trim(), email };
}

function emailAddresses(value: string) {
  return Array.from(value.matchAll(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi), (match) =>
    match[0].toLowerCase(),
  );
}

function messageDirection(
  message: GmailInquiryForward,
  source: string,
  leadEmail: string,
) {
  const normalizedLead = leadEmail.trim().toLowerCase();
  if (normalizedLead && emailAddresses(message.from).includes(normalizedLead)) {
    return "Received email";
  }
  if (normalizedLead && emailAddresses(message.to).includes(normalizedLead)) {
    return "Sent email";
  }
  if (/\b(gmail|outlook|email)\b/i.test(source)) return "Inbound email";
  return "Source evidence";
}

function normalizedFooterKey(value: string) {
  return value.toLocaleLowerCase().replace(/\s+/g, " ").trim();
}

function prepareMessage(
  message: GmailInquiryForward,
  seenFooters: Set<string>,
): PreparedMessage {
  const body = splitGmailBodyForDisplay(message.body);
  const footers = body.footers.filter((footer) => {
    const key = normalizedFooterKey(footer);
    if (!key || seenFooters.has(key)) return false;
    seenFooters.add(key);
    return true;
  });
  return {
    message,
    body: { ...body, footers },
    forwarded: message.forwarded ? prepareMessage(message.forwarded, seenFooters) : null,
  };
}

function safeHref(value: string) {
  try {
    const url = new URL(value);
    return ["http:", "https:", "mailto:"].includes(url.protocol) ? value : null;
  } catch {
    return null;
  }
}

function linkifiedText(value: string): ReactNode[] {
  const matcher = /(https?:\/\/[^\s<>"']+|mailto:[^\s<>"']+|[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})/gi;
  const nodes: ReactNode[] = [];
  let cursor = 0;
  for (const match of value.matchAll(matcher)) {
    const raw = match[0];
    const start = match.index ?? cursor;
    const link = raw.replace(/[),.!?:;}\]]+$/g, "");
    const trailing = raw.slice(link.length);
    const href = safeHref(link) || (link.includes("@") ? safeHref(`mailto:${link}`) : null);
    if (!href) continue;
    if (start > cursor) nodes.push(value.slice(cursor, start));
    nodes.push(<a href={href} key={`${start}-${link}`} rel="noopener noreferrer" target="_blank">{link}</a>);
    if (trailing) nodes.push(trailing);
    cursor = start + raw.length;
  }
  if (cursor < value.length) nodes.push(value.slice(cursor));
  return nodes;
}

function BodyText({ text }: { text: string }) {
  const paragraphs = text.split(/\n\s*\n+/).filter((paragraph) => paragraph.trim());
  if (!paragraphs.length) return null;
  return <div className="gmail-inquiry-body-content">
    {paragraphs.map((paragraph, index) => {
      const lines = paragraph.split("\n");
      const listItems = lines.map((line) => line.match(/^\s*(?:[-*•]|\d+[.)])\s+(.+)$/));
      if (listItems.every(Boolean)) {
        const ordered = lines.every((line) => /^\s*\d+[.)]\s+/.test(line));
        const List = ordered ? "ol" : "ul";
        return <List key={`list-${index}`}>{listItems.map((item, itemIndex) => (
          <li key={`${index}-${itemIndex}`}>{linkifiedText(item?.[1] || "")}</li>
        ))}</List>;
      }
      return <p key={`paragraph-${index}`}>{lines.map((line, lineIndex) => (
        <Fragment key={`line-${lineIndex}`}>{lineIndex > 0 && <br />}{linkifiedText(line)}</Fragment>
      ))}</p>;
    })}
  </div>;
}

function Recipients({ message }: { message: GmailInquiryForward }) {
  const recipients = [
    ["To", message.to],
    ["Cc", message.cc || ""],
  ].filter(([, value]) => Boolean(value.trim()));
  if (!recipients.length) return null;
  return <div className="gmail-inquiry-recipients">
    {recipients.map(([label, value]) => (
      <div className="gmail-inquiry-recipient" key={label}>
        <span>{label}</span><div>{linkifiedText(value)}</div>
      </div>
    ))}
  </div>;
}

function EmailBody({
  prepared,
  source,
  leadEmail,
}: {
  prepared: PreparedMessage;
  source: string;
  leadEmail: string;
}) {
  const { body, forwarded } = prepared;
  const hasQuotedContent = body.quoted.length > 0 || forwarded !== null;
  return <div className="gmail-inquiry-message-body">
    {body.primary
      ? <BodyText text={body.primary} />
      : !hasQuotedContent && body.footers.length === 0
        && <p className="gmail-inquiry-empty">No message text was included.</p>}
    {hasQuotedContent && <details className="gmail-inquiry-disclosure gmail-inquiry-quoted">
      <summary>Quoted/forwarded content</summary>
      <div className="gmail-inquiry-disclosure-content">
        {body.quoted.map((quote, index) => (
          <blockquote className="gmail-inquiry-quote" key={`quote-${index}`}><BodyText text={quote} /></blockquote>
        ))}
        {forwarded && <ForwardedMessage prepared={forwarded} source={source} leadEmail={leadEmail} />}
      </div>
    </details>}
    {body.footers.length > 0 && <details className="gmail-inquiry-disclosure gmail-inquiry-footer">
      <summary>Show footer and disclaimer</summary>
      <div className="gmail-inquiry-disclosure-content">
        {body.footers.map((footer, index) => <BodyText key={`footer-${index}`} text={footer} />)}
      </div>
    </details>}
  </div>;
}

function ForwardedMessage({
  prepared,
  source,
  leadEmail,
}: {
  prepared: PreparedMessage;
  source: string;
  leadEmail: string;
}) {
  const { message } = prepared;
  const sender = senderDisplay(message.from);
  return <section className="gmail-inquiry-forward">
    <div className="gmail-inquiry-forward-label">Forwarded email</div>
    <div className="gmail-inquiry-forward-meta">
      <div><span>From</span><strong>{sender.name || sender.email || "Unknown sender"}</strong>{sender.email && <small>{sender.email}</small>}</div>
      {message.date && <div><span>{messageDirection(message, source, leadEmail)}</span><strong>{formatDate(message.date)}</strong></div>}
      {message.subject && <div className="gmail-inquiry-forward-subject"><span>Subject</span><strong>{message.subject}</strong></div>}
    </div>
    <EmailBody prepared={prepared} source={source} leadEmail={leadEmail} />
    <Recipients message={message} />
  </section>;
}

function EmailMessage({
  prepared,
  index,
  label,
  source,
  leadEmail,
  receivedAt,
  showTypeBadge = true,
}: {
  prepared: PreparedMessage;
  index: number;
  label: string;
  source: string;
  leadEmail: string;
  receivedAt: string;
  showTypeBadge?: boolean;
}) {
  const { message } = prepared;
  const sender = senderDisplay(message.from);
  if (!sender.name && !sender.email && leadEmail) sender.email = leadEmail;
  const initials = (sender.name || sender.email || "E")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
  const direction = messageDirection(message, source, leadEmail);
  const date = message.date || (index === 0 ? receivedAt : "");
  const dateLabel = direction.startsWith("Sent") ? "Sent" : direction.includes("Received") || direction.includes("Inbound") ? "Received" : "Date";
  return <article className="gmail-inquiry-message">
    <header className="gmail-inquiry-message-header">
      <span className="gmail-inquiry-avatar" aria-hidden="true">{initials}</span>
      <div className="gmail-inquiry-sender">
        <strong>{sender.name || sender.email || "Sender"}</strong>
        {sender.email && <small>{sender.email}</small>}
      </div>
      <div className="gmail-inquiry-message-meta">
        <div className="gmail-inquiry-badges">
          {showTypeBadge && (label === "Latest email" || label === "Earlier email") && <span className={`gmail-inquiry-type-badge${label === "Latest email" ? " latest" : ""}`}>{label}</span>}
          <span className="gmail-inquiry-direction-badge">{direction}</span>
        </div>
        {date && <time dateTime={dateTimeValue(date)}><span>{dateLabel}</span> {formatDate(date)}</time>}
      </div>
    </header>
    {message.subject && <div className="gmail-inquiry-subject">
      <h4>{message.subject}</h4>
    </div>}
    <EmailBody prepared={prepared} source={source} leadEmail={leadEmail} />
    <Recipients message={message} />
  </article>;
}

function RawEmail({
  message,
  label = "View raw email",
}: {
  message: string;
  label?: string;
}) {
  return <details className="gmail-inquiry-raw">
    <summary>{label}</summary>
    <pre>{message}</pre>
  </details>;
}

export function GmailInquiryView({
  message,
  source = "",
  leadEmail = "",
  receivedAt = "",
  displayAsContext = false,
}: {
  message: string;
  source?: string;
  leadEmail?: string;
  receivedAt?: string;
  displayAsContext?: boolean;
}) {
  const inquiry = displayAsContext ? null : parseGmailInquiry(message);
  if (!inquiry) {
    const readableText = displayAsContext
      ? message
      : cleanGmailInquiryText(message);
    const fallbackMessage: GmailInquiryMessage = {
      from: "",
      to: "",
      cc: "",
      date: receivedAt,
      subject: "",
      body: readableText || message,
      forwarded: null,
    };
    const prepared = prepareMessage(fallbackMessage, new Set());
    return <div className="gmail-inquiry-view">
      <EmailMessage
        prepared={prepared}
        index={0}
        label={displayAsContext ? "Inquiry / process context" : "Original inquiry"}
        source={source}
        leadEmail={leadEmail}
        receivedAt={receivedAt}
      />
      <RawEmail
        message={message}
        label={displayAsContext ? "View full submitted context" : "View raw email"}
      />
    </div>;
  }

  const role = inquiry.role.replace(/\b\w/g, (letter) => letter.toUpperCase());
  const seenFooters = new Set<string>();
  const preparedMessages = inquiry.messages.map((item) => prepareMessage(item, seenFooters));
  const [original, ...earlierMessages] = preparedMessages;

  return <div className="gmail-inquiry-view">
    <div className="gmail-inquiry-context">
      <div><span className="eyebrow">Source conversation</span><strong>{inquiry.messages.length} {inquiry.messages.length === 1 ? "email" : "emails"}</strong></div>
      {role && <span className="gmail-inquiry-role">{role}</span>}
      {inquiry.context && <p>{inquiry.context}</p>}
    </div>
    <div className="gmail-inquiry-messages">
      {original && <EmailMessage
        prepared={original}
        index={0}
        label="Latest email"
        source={source}
        leadEmail={leadEmail}
        receivedAt={receivedAt}
        showTypeBadge={inquiry.messages.length > 1}
      />}
      {earlierMessages.length > 0 && <details className="gmail-inquiry-history">
        <summary>View {earlierMessages.length} earlier {earlierMessages.length === 1 ? "email" : "emails"}</summary>
        <div className="gmail-inquiry-history-messages">
          {earlierMessages.map((item, index) => <EmailMessage
            key={`${index + 1}-${item.message.date}-${item.message.subject}`}
            prepared={item}
            index={index + 1}
            label="Earlier email"
            source={source}
            leadEmail={leadEmail}
            receivedAt=""
            showTypeBadge
          />)}
        </div>
      </details>}
    </div>
    <RawEmail message={message} />
  </div>;
}
