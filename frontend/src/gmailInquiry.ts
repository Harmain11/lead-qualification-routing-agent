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
    } else if (currentHeader && /^\s/.test(line)) {
      headers[currentHeader] += ` ${line.trim()}`;
    }
  }
  return { headers, body };
}

function parseForwardedMessage(body: string) {
  const marker = /(?:^|\n)-{5,}\s*Forwarded message\s*-{5,}\s*(?:\n|$)/i.exec(body);
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
  const source = message.replace(/\r\n/g, "\n");
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
