import "./_group.css";

type Message = { from: string; to: string; date: string; subject: string; body: string };
type Inquiry = { role: string; context: string; messages: Message[] };

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

const headerNames = "From|To|Cc|Bcc|Date|Subject|Reply-To|References|In-Reply-To|Message-ID|MIME-Version|Content-Type|Content-Transfer-Encoding";

function parseHeadersAndBody(raw: string) {
  const source = raw.replace(/\r\n?/g, "\n").replace(new RegExp(`[\\t ]+(?=(?:${headerNames}):\\s)`, "gi"), "\n");
  const separator = source.search(/\n\s*\n/);
  const lines = (separator < 0 ? source : source.slice(0, separator)).split("\n");
  const headers: Record<string, string> = {};
  let current = "";
  let bodyStart = -1;
  for (const [index, line] of lines.entries()) {
    const match = line.match(/^([A-Za-z-]+):\s*(.*)$/);
    if (match) { current = match[1].toLowerCase(); headers[current] = match[2].trim(); }
    else if (current && /^\s/.test(line)) headers[current] += ` ${line.trim()}`;
    else if (line.trim()) { bodyStart = index; break; }
  }
  const body = separator < 0
    ? bodyStart < 0 ? "" : lines.slice(bodyStart).join("\n").trim()
    : source.slice(separator).replace(/^\n\s*/, "").trim();
  return { headers, body };
}

function parseGmailInquiry(raw: string): Inquiry | null {
  const source = raw.replace(/\r\n?/g, "\n").replace(/[ \t]*--- Gmail message ([^ ]+) ---[ \t]*/g, "\n--- Gmail message $1 ---\n");
  const markers = Array.from(source.matchAll(/^--- Gmail message ([^ ]+) ---$/gm));
  if (!markers.length) return null;
  const prefix = source.slice(0, markers[0].index ?? 0);
  const role = prefix.match(/Thread role:\s*([^\n.]+?)(?:\.|$)/i)?.[1].trim().replaceAll("_", " ") ?? "";
  const context = prefix.replace(/Original thread:\s*/i, "").replace(/Thread role:[^\n.]+\.?/gi, "").replace(/\s+/g, " ").trim();
  const messages = markers.map((marker, index) => {
    const start = (marker.index ?? 0) + marker[0].length;
    const end = markers[index + 1]?.index ?? source.length;
    const { headers, body } = parseHeadersAndBody(source.slice(start, end).trim());
    return { from: headers.from || "", to: headers.to || "", date: headers.date || "", subject: headers.subject || "", body };
  });
  return { role, context, messages };
}

function formatDate(value: string) {
  const date = new Date(value);
  return value && !Number.isNaN(date.getTime()) ? date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : value;
}

function InquiryMessage({ item, original = false }: { item: Message; original?: boolean }) {
  const match = item.from.match(/^\s*(.*?)\s*<([^<>]+)>\s*$/);
  const name = match?.[1]?.trim() || item.from;
  const address = match?.[2]?.trim() || "";
  return <article className="gmail-inquiry-message"><header className="gmail-inquiry-message-header">
    <span className="gmail-inquiry-avatar" aria-hidden="true">{name.split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "E"}</span>
    <div className="gmail-inquiry-sender"><strong>{name || "Email sender"}</strong>{address && <small>{address}</small>}</div>
    <div className="gmail-inquiry-message-meta"><span>{original ? "Original inquiry" : "Conversation reply"}</span>{item.date && <time>{formatDate(item.date)}</time>}</div>
    </header>
    {item.subject && <h4 className="gmail-inquiry-subject">{item.subject}</h4>}
    {item.to && <p className="gmail-inquiry-recipient"><span>To</span>{item.to}</p>}
    <p className="gmail-inquiry-message-body">{item.body || "No message text was included."}</p>
  </article>;
}

export function CleanInquiry() {
  const inquiry = parseGmailInquiry(message);
  if (!inquiry) return <div className="inquiry-mockup"><h2>Original inquiry</h2><div className="original-inquiry-plain">{message}</div></div>;
  const [original, ...replies] = inquiry.messages;
  return <div className="inquiry-mockup"><h2>Original inquiry</h2><div className="gmail-inquiry-view">
    <div className="gmail-inquiry-context"><div><span className="eyebrow">Source conversation</span><strong>{inquiry.messages.length} {inquiry.messages.length === 1 ? "email" : "emails"}</strong></div>{inquiry.role && <span className="gmail-inquiry-role">{inquiry.role.replace(/\b\w/g, (letter) => letter.toUpperCase())}</span>}{inquiry.context && <p>{inquiry.context}</p>}</div>
    <div className="gmail-inquiry-messages">
      {original && <InquiryMessage item={original} original />}
      {replies.length > 0 && <details className="gmail-inquiry-history"><summary>View {replies.length} earlier {replies.length === 1 ? "email" : "emails"}</summary><div className="gmail-inquiry-history-messages">{replies.map((item, index) => <InquiryMessage item={item} key={`${index}-${item.date}-${item.subject}`} />)}</div></details>}
    </div>
    <details className="gmail-inquiry-raw"><summary>View full original source</summary><pre>{message}</pre></details>
  </div></div>;
}
