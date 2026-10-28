import "./_group.css";

type Forward = { from: string; to: string; date: string; subject: string; body: string };
type EmailMessage = Forward & { forwarded: Forward | null };
type ParsedInquiry = { role: string; context: string; messages: EmailMessage[] };

function parseHeadersAndBody(source: string) {
  const separator = source.search(/\n\s*\n/);
  const headerText = separator < 0 ? "" : source.slice(0, separator);
  const body = separator < 0 ? source.trim() : source.slice(separator).replace(/^\n\s*/, "").trim();
  const headers: Record<string, string> = {};
  let currentHeader = "";
  for (const line of headerText.split(/\r?\n/)) {
    const match = line.match(/^([A-Za-z-]+):\s*(.*)$/);
    if (match) {
      currentHeader = match[1].toLowerCase();
      headers[currentHeader] = match[2].trim();
    } else if (currentHeader && /^\s/.test(line)) headers[currentHeader] += ` ${line.trim()}`;
  }
  return { headers, body };
}

function parseForwardedMessage(body: string) {
  const marker = /(?:^|\n)-{5,}\s*Forwarded message\s*-{5,}\s*(?:\n|$)/i.exec(body);
  if (!marker) return { body: body.trim(), forwarded: null };
  const before = body.slice(0, marker.index).trim();
  const forwardedSource = body.slice(marker.index + marker[0].length);
  const { headers, body: forwardedBody } = parseHeadersAndBody(forwardedSource);
  if (!headers.from && !headers.subject && !headers.to) return { body: body.trim(), forwarded: null };
  return {
    body: before,
    forwarded: { from: headers.from || "", to: headers.to || "", date: headers.date || "", subject: headers.subject || "", body: forwardedBody },
  };
}

function parseGmailInquiry(message: string): ParsedInquiry | null {
  const source = message.replace(/\r\n/g, "\n");
  const markers = Array.from(source.matchAll(/^--- Gmail message ([^ ]+) ---$/gm));
  if (!markers.length) return null;
  const prefix = source.slice(0, markers[0].index ?? 0);
  const roleMatch = prefix.match(/Thread role:\s*([^\n.]+?)(?:\.|$)/i);
  const context = prefix.replace(/Original thread:\s*/i, "").replace(/Thread role:[^\n.]+\.?/gi, "").replace(/\s+/g, " ").trim();
  const messages = markers.map((marker, index): EmailMessage => {
    const start = (marker.index ?? 0) + marker[0].length;
    const end = markers[index + 1]?.index ?? source.length;
    const { headers, body } = parseHeadersAndBody(source.slice(start, end).trim());
    const forwarded = parseForwardedMessage(body);
    return {
      from: headers.from || "", to: headers.to || "", date: headers.date || "", subject: headers.subject || "",
      body: forwarded.body, forwarded: forwarded.forwarded,
    };
  });
  return { role: roleMatch?.[1].trim().replaceAll("_", " ") || "", context, messages };
}

const message = `Thread role: direct prospect. Referred by the website intake form.
Original thread: --- Gmail message prospect-100 ---
From: Pat Example <pat@example.test>
To: sales@corral.example
Date: Tue, 20 Oct 2026 09:00:00 +0000
Subject: Pricing request for a routing project
References: <form@example.test>
MIME-Version: 1.0
Content-Type: text/plain; charset=UTF-8

Hi team, we are evaluating a new lead-routing platform for a 25-person sales team. Could you send pricing and let us know whether it integrates with our CRM?

--- Gmail message reply-200 ---
From: Sales Team <sales@corral.example>
To: Pat Example <pat@example.test>
Date: Tue, 20 Oct 2026 11:15:00 +0000
Subject: Re: Pricing request for a routing project
MIME-Version: 1.0
Content-Type: text/plain; charset=UTF-8

Thanks, Pat. We can share a proposal after a short discovery call.`;

function formatDate(value: string) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function senderDisplay(value: string) {
  const match = value.match(/^\s*(.*?)\s*<([^<>]+)>\s*$/);
  return match ? { name: match[1].replace(/^["']|["']$/g, "").trim(), email: match[2].trim() } : { name: value.trim(), email: "" };
}

function MessageText({ body }: { body: string }) {
  const text = body.trim();
  if (!text) return <p className="gmail-inquiry-empty">No message text was included.</p>;
  if (text.length <= 720) return <p className="gmail-inquiry-message-body">{text}</p>;
  return <div className="gmail-inquiry-message-body"><p>{text.slice(0, 720).trimEnd()}…</p><details><summary>Read full message</summary><pre>{text}</pre></details></div>;
}

function ForwardedMessage({ message }: { message: Forward }) {
  const sender = senderDisplay(message.from);
  return <section className="gmail-inquiry-forward"><div className="gmail-inquiry-forward-label"><span aria-hidden="true">↳</span> Forwarded message</div>
    <div className="gmail-inquiry-forward-meta"><div><span>From</span><strong>{sender.name || sender.email || "Unknown sender"}</strong>{sender.email && <small>{sender.email}</small>}</div>
      {message.date && <div><span>Date</span><strong>{formatDate(message.date)}</strong></div>}
      {message.subject && <div className="gmail-inquiry-forward-subject"><span>Subject</span><strong>{message.subject}</strong></div>}
      {message.to && <div className="gmail-inquiry-forward-subject"><span>To</span><strong>{message.to}</strong></div>}</div>
    <MessageText body={message.body} />
  </section>;
}

function EmailMessageCard({ message, index }: { message: EmailMessage; index: number }) {
  const sender = senderDisplay(message.from);
  return <article className="gmail-inquiry-message"><header className="gmail-inquiry-message-header">
    <span className="gmail-inquiry-avatar" aria-hidden="true">{(sender.name || sender.email || "G").split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toUpperCase()}</span>
    <div className="gmail-inquiry-sender"><strong>{sender.name || sender.email || "Email sender"}</strong>{sender.email && <small>{sender.email}</small>}</div>
    <div className="gmail-inquiry-message-meta"><span>Message {index + 1}</span>{message.date && <time>{formatDate(message.date)}</time>}</div>
    </header>
    {message.subject && <h4 className="gmail-inquiry-subject">{message.subject}</h4>}
    {message.to && <p className="gmail-inquiry-recipient"><span>To</span>{message.to}</p>}
    {message.body && <MessageText body={message.body} />}
    {message.forwarded && <ForwardedMessage message={message.forwarded} />}
  </article>;
}

export function Current() {
  const inquiry = parseGmailInquiry(message);
  if (!inquiry) return <div className="inquiry-mockup"><h2>Original inquiry</h2><div className="original-inquiry-plain">{message}</div></div>;
  return <div className="inquiry-mockup"><h2>Original inquiry</h2><div className="gmail-inquiry-view">
    <div className="gmail-inquiry-context"><div><span className="eyebrow">Source conversation</span><strong>{inquiry.messages.length} emails</strong></div>{inquiry.role && <span className="gmail-inquiry-role">{inquiry.role}</span>}{inquiry.context && <p>{inquiry.context}</p>}</div>
    <div className="gmail-inquiry-messages">{inquiry.messages.map((item, index) => <EmailMessageCard key={`${index}-${item.date}-${item.subject}`} message={item} index={index} />)}</div>
    <details className="gmail-inquiry-raw"><summary>View full original source</summary><pre>{message}</pre></details>
  </div></div>;
}
