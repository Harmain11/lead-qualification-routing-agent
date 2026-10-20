import "./_group.css";

type GmailInquiryForward = {
  from: string;
  to: string;
  cc: string;
  date: string;
  subject: string;
  body: string;
  forwarded: GmailInquiryForward | null;
};

type GmailInquiryMessage = GmailInquiryForward & {
  forwarded: GmailInquiryForward | null;
};

type ParsedGmailInquiry = {
  role: string;
  context: string;
  messages: GmailInquiryMessage[];
};

const GMAIL_HEADER_NAMES = [
  "From", "To", "Cc", "Bcc", "Date", "Subject", "Reply-To", "References",
  "In-Reply-To", "Message-ID", "MIME-Version", "Content-Type",
  "Content-Transfer-Encoding",
].join("|");

function normalizeHeaderBoundaries(source: string) {
  return source
    .replace(/\r\n?/g, "\n")
    .replace(new RegExp(`[\\t ]+(?=(?:${GMAIL_HEADER_NAMES}):\\s)`, "gi"), "\n");
}

function parseHeadersAndBody(source: string) {
  const normalized = normalizeHeaderBoundaries(source);
  const separator = normalized.search(/\n\s*\n/);
  const headerText = separator < 0 ? normalized : normalized.slice(0, separator);
  let body = separator < 0 ? "" : normalized.slice(separator).replace(/^\n\s*/, "").trim();
  const headers: Record<string, string> = {};
  let currentHeader = "";
  const lines = headerText.split(/\n/);
  let firstBodyLine = -1;
  for (const [index, line] of lines.entries()) {
    const match = line.match(/^([A-Za-z-]+):\s*(.*)$/);
    if (match) {
      currentHeader = match[1].toLowerCase();
      headers[currentHeader] = match[2].trim();
    } else if (currentHeader && /^\s/.test(line)) {
      headers[currentHeader] += ` ${line.trim()}`;
    } else if (line.trim()) {
      firstBodyLine = index;
      break;
    }
  }
  if (separator < 0 && firstBodyLine >= 0) {
    body = lines.slice(firstBodyLine).join("\n").trim();
  }
  return { headers, body };
}

function parseForwardedMessage(body: string): { body: string; forwarded: GmailInquiryForward | null } {
  const marker = /-{5,}\s*Forwarded message\s*-{5,}\s*/i.exec(body);
  if (!marker) return { body: body.trim(), forwarded: null };
  const before = body.slice(0, marker.index).trim();
  const forwardedSource = body.slice(marker.index + marker[0].length);
  const { headers, body: forwardedBody } = parseHeadersAndBody(forwardedSource);
  if (!headers.from && !headers.subject && !headers.to) {
    return { body: body.trim(), forwarded: null };
  }
  const nested = parseForwardedMessage(forwardedBody);
  return {
    body: before,
    forwarded: {
      from: headers.from || "",
      to: headers.to || "",
      cc: headers.cc || "",
      date: headers.date || "",
      subject: headers.subject || "",
      body: nested.body,
      forwarded: nested.forwarded,
    },
  };
}

function parseGmailInquiry(message: string): ParsedGmailInquiry | null {
  const source = message
    .replace(/\r\n?/g, "\n")
    .replace(/[ \t]*--- Gmail message ([^ ]+) ---[ \t]*/g, "\n--- Gmail message $1 ---\n");
  const markers = Array.from(source.matchAll(/^--- Gmail message ([^ ]+) ---$/gm));
  if (!markers.length) return null;

  const prefix = source.slice(0, markers[0].index ?? 0);
  const roleMatch = prefix.match(/Thread role:\s*([^\n.]+?)(?:\.|$)/i);
  const context = prefix
    .replace(/Original thread:\s*/i, "")
    .replace(/Thread role:[^\n.]+\.?/gi, "")
    .replace(/\s+/g, " ")
    .trim();
  const messages = markers.map((marker, index): GmailInquiryMessage => {
    const start = (marker.index ?? 0) + marker[0].length;
    const end = markers[index + 1]?.index ?? source.length;
    const { headers, body } = parseHeadersAndBody(source.slice(start, end).trim());
    const forwarded = parseForwardedMessage(body);
    return {
      from: headers.from || "",
      to: headers.to || "",
      cc: headers.cc || "",
      date: headers.date || "",
      subject: headers.subject || "",
      body: forwarded.body,
      forwarded: forwarded.forwarded,
    };
  });

  return {
    role: roleMatch?.[1].trim().replaceAll("_", " ") || "",
    context,
    messages,
  };
}

function senderDisplay(value: string) {
  const match = value.match(/^\s*(.*?)\s*<([^<>]+)>\s*$/);
  if (match) return { name: match[1].replace(/^["']|["']$/g, "").trim(), email: match[2].trim() };
  return { name: value.trim(), email: "" };
}

function formatDate(value: string) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

const inquirySource = `Original thread: Website inquiry received through the Northstar Studio contact form.
Thread role: inbound_lead. Parsed from the original Gmail conversation.

--- Gmail message original ---
From: Maya Chen <maya.chen@northstarstudio.co>
To: hello@lumenworks.io
Date: Tue, 18 Feb 2025 09:42:00 -0800
Subject: Re: A question about your customer onboarding workflow
Message-ID: <CAJ8m9-northstar-inquiry@gmail.com>
MIME-Version: 1.0
Content-Type: text/plain; charset="UTF-8"

Hi Lumenworks team,

We’re reviewing our customer onboarding process before a new product launch in May. Right now, handoffs between sales and implementation live in a mix of spreadsheets and email, and we’re losing context as soon as a deal closes.

Could you share how Lumenworks handles the first 30 days after a customer signs? I’m especially interested in how your team keeps owners, next steps, and customer goals visible without adding another system for our team to maintain.

We have 18 people across sales and customer success, and we’d be glad to set aside 25 minutes next week if this sounds like a fit.

Thanks,
Maya
Operations Director, Northstar Studio

---
This email and any attachments are intended only for the named recipient. Please do not forward or distribute without permission. If you received this message in error, reply to the sender and remove it from your inbox.

---------- Forwarded message ----------
From: Evan Brooks <evan.brooks@northstarstudio.co>
To: Maya Chen <maya.chen@northstarstudio.co>
Date: Mon, 17 Feb 2025 16:18:00 -0800
Subject: Fwd: onboarding workflow notes

Maya — this is the team I mentioned after the RevOps roundtable. Their onboarding workspace looked close to what we need. Might be worth asking about a lightweight pilot.

---------- Forwarded message ----------
From: Priya Nair <priya@lumenworks.io>
To: Evan Brooks <evan.brooks@northstarstudio.co>
Date: Mon, 17 Feb 2025 13:05:00 -0800
Subject: Re: onboarding workflow notes

Hi Evan,

Sharing a short overview of our customer launch workspace. It gives sales and delivery a single place for kickoff notes, ownership, and agreed outcomes.

Best,
Priya`;

function splitDisclaimer(body: string) {
  const delimiter = /\n-{2,}\n(?=(?:This email|This message|Confidentiality))/i.exec(body);
  if (!delimiter) return { readable: body.trim(), disclaimer: "" };
  return {
    readable: body.slice(0, delimiter.index).trim(),
    disclaimer: body.slice(delimiter.index + delimiter[0].length).trim(),
  };
}

function initials(value: string) {
  return (value || "Email sender")
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
}

function ForwardedMessage({
  message,
  nested = false,
}: {
  message: GmailInquiryForward;
  nested?: boolean;
}) {
  const sender = senderDisplay(message.from);
  return (
    <section className={`lead-evidence-forward${nested ? " is-nested" : ""}`} aria-label="Forwarded email">
      <h3 className="lead-evidence-forward-label">Forwarded message</h3>
      <div className="lead-evidence-forward-grid">
        <div className="lead-evidence-forward-field">
          <span>From</span>
          <strong>{sender.name || sender.email || "Unknown sender"}</strong>
          {sender.email && <small>{sender.email}</small>}
        </div>
        {message.date && <div className="lead-evidence-forward-field"><span>Date</span><strong>{formatDate(message.date)}</strong></div>}
        {message.to && <div className="lead-evidence-forward-field"><span>To</span><strong>{message.to}</strong></div>}
        {message.cc && <div className="lead-evidence-forward-field"><span>Cc</span><strong>{message.cc}</strong></div>}
        {message.subject && <div className="lead-evidence-forward-field lead-evidence-forward-subject"><span>Subject</span><strong>{message.subject}</strong></div>}
      </div>
      <div className="lead-evidence-forward-body">{message.body || "No message text was included."}</div>
      {message.forwarded && (
        <details className="lead-evidence-nested-forward">
          <summary>Quoted/forwarded content</summary>
          <ForwardedMessage message={message.forwarded} nested />
        </details>
      )}
    </section>
  );
}

function EmailMessage({ message, label }: { message: GmailInquiryMessage; label: string }) {
  const sender = senderDisplay(message.from);
  const { readable, disclaimer } = splitDisclaimer(message.body);

  return (
    <article className="lead-evidence-message">
      <header className="lead-evidence-message-head">
        <span className="lead-evidence-avatar" aria-hidden="true">{initials(sender.name || sender.email)}</span>
        <div className="lead-evidence-sender">
          <div className="lead-evidence-sender-name">{sender.name || sender.email || "Email sender"}</div>
          {sender.email && <span className="lead-evidence-sender-email">{sender.email}</span>}
        </div>
        <div className="lead-evidence-message-meta">
          <div className="lead-evidence-direction"><span className="lead-evidence-direction-mark" aria-hidden="true">←</span> Inbound · {label}</div>
          {message.date && <time dateTime={new Date(message.date).toISOString()}>{formatDate(message.date)}</time>}
        </div>
      </header>

      {message.subject && <h2 className="lead-evidence-subject">{message.subject}</h2>}
      {message.to && <p className="lead-evidence-recipients"><span className="lead-evidence-recipients-label">To</span>{message.to}</p>}
      {message.cc && <p className="lead-evidence-recipients"><span className="lead-evidence-recipients-label">Cc</span>{message.cc}</p>}

      <div className="lead-evidence-body">
        {readable ? readable.split(/\n{2,}/).map((paragraph, index) => (
          <p key={`${index}-${paragraph.slice(0, 16)}`}>{paragraph}</p>
        )) : <p>No message text was included.</p>}
      </div>

      {message.forwarded && (
        <details className="lead-evidence-quoted">
          <summary>Quoted/forwarded content</summary>
          <ForwardedMessage message={message.forwarded} />
        </details>
      )}
      {disclaimer && (
        <details className="lead-evidence-footer">
          <summary>Show footer and disclaimer</summary>
          <div className="lead-evidence-footer-content">{disclaimer}</div>
        </details>
      )}
    </article>
  );
}

export function LeadEmailEvidence() {
  const inquiry = parseGmailInquiry(inquirySource);
  if (!inquiry) return null;
  const [original] = inquiry.messages;
  const role = inquiry.role.replace(/\b\w/g, (letter) => letter.toUpperCase());

  return (
    <main className="lead-evidence-preview lead-evidence-page">
      <div className="lead-evidence-shell">
        <p className="lead-evidence-kicker">Lead details / source evidence</p>
        <div className="lead-evidence-heading">
          <div>
            <h1>Original inquiry</h1>
            <p>Review the message as it arrived, with its original context intact.</p>
          </div>
          <span className="lead-evidence-status">Source verified · Gmail</span>
        </div>

        <section className="lead-evidence-conversation" aria-label="Email source conversation">
          <div className="lead-evidence-context">
            <div className="lead-evidence-context-main">
              <span className="lead-evidence-source"><span className="lead-evidence-source-mark" aria-hidden="true">G</span> Gmail source</span>
              <span className="lead-evidence-separator" aria-hidden="true" />
              <span className="lead-evidence-thread-count">{inquiry.messages.length} email in source thread</span>
            </div>
            {role && <span className="lead-evidence-role">{role}</span>}
          </div>
          {inquiry.context && <p className="lead-evidence-context-note">{inquiry.context}</p>}
          {original && <EmailMessage message={original} label="Original inquiry" />}
          <details className="lead-evidence-raw">
            <summary>View raw email</summary>
            <pre>{inquirySource}</pre>
          </details>
        </section>
        <p className="lead-evidence-note">Email content is shown as received. Forwarded text and the original source remain available for review.</p>
      </div>
    </main>
  );
}
