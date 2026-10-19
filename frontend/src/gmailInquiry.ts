export type GmailInquiryForward = {
  from: string;
  to: string;
  date: string;
  subject: string;
  body: string;
};

export type GmailInquiryMessage = GmailInquiryForward & {
  forwarded: GmailInquiryForward | null;
};

export type ParsedGmailInquiry = {
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

function parseForwardedMessage(body: string) {
  const marker = /-{5,}\s*Forwarded message\s*-{5,}\s*/i.exec(body);
  if (!marker) return { body: body.trim(), forwarded: null };
  const before = body.slice(0, marker.index).trim();
  const forwardedSource = body.slice(marker.index + marker[0].length);
  const { headers, body: forwardedBody } = parseHeadersAndBody(forwardedSource);
  if (!headers.from && !headers.subject && !headers.to) {
    return { body: body.trim(), forwarded: null };
  }
  return {
    body: before,
    forwarded: {
      from: headers.from || "",
      to: headers.to || "",
      date: headers.date || "",
      subject: headers.subject || "",
      body: forwardedBody,
    },
  };
}

export function parseGmailInquiry(message: string): ParsedGmailInquiry | null {
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

export function cleanGmailInquiryText(message: string): string {
  const headerLine = new RegExp(`^\\s*(?:${GMAIL_HEADER_NAMES}):\\s*.*$`, "i");
  return message
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .filter((line) =>
      !headerLine.test(line)
      && !/^\s*--- Gmail message [^ ]+ ---\s*$/i.test(line)
      && !/^\s*Thread role:\s*/i.test(line)
      && !/^\s*Original thread:\s*/i.test(line),
    )
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}
