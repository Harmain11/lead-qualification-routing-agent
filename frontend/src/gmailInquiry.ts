export type GmailInquiryForward = {
  from: string;
  to: string;
  cc?: string;
  date: string;
  subject: string;
  body: string;
  forwarded?: GmailInquiryForward | null;
};

export type GmailInquiryMessage = GmailInquiryForward & {
  forwarded: GmailInquiryForward | null;
};

export type ParsedGmailInquiry = {
  role: string;
  context: string;
  messages: GmailInquiryMessage[];
};

type ForwardedMessageParseResult = {
  body: string;
  forwarded: GmailInquiryForward | null;
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

function parseForwardedMessage(body: string): ForwardedMessageParseResult {
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

export function parseGmailInquiry(message: string): ParsedGmailInquiry | null {
  const source = message
    .replace(/\r\n?/g, "\n")
    .replace(/[ \t]*--- Gmail message ([^ ]+) ---[ \t]*/g, "\n--- Gmail message $1 ---\n");
  const markers = Array.from(source.matchAll(/^--- Gmail message ([^ ]+) ---$/gm));
  if (!markers.length) {
    const firstHeader = source.search(
      /^\s*(?:From|To|Cc|Bcc|Date|Subject):\s/im,
    );
    if (firstHeader < 0) return null;
    const prefix = source.slice(0, firstHeader);
    const headerSource = source.slice(firstHeader).trim();
    const { headers, body } = parseHeadersAndBody(headerSource);
    if (!headers.from && !headers.to && !headers.subject && !headers.date) return null;
    const forwarded = parseForwardedMessage(body);
    const roleMatch = prefix.match(/Thread role:\s*([^\n.]+?)(?:\.|$)/i);
    const context = prefix
      .replace(/Original thread:\s*/i, "")
      .replace(/Thread role:[^\n.]+\.?/gi, "")
      .replace(/\s+/g, " ")
      .trim();
    return {
      role: roleMatch?.[1].trim().replaceAll("_", " ") || "",
      context,
      messages: [{
        from: headers.from || "",
        to: headers.to || "",
        cc: headers.cc || "",
        date: headers.date || "",
        subject: headers.subject || "",
        body: forwarded.body,
        forwarded: forwarded.forwarded,
      }],
    };
  }

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

export type GmailBodyDisplaySections = {
  primary: string;
  quoted: string[];
  footers: string[];
};

const EMAIL_BLOCK_TAGS = new Set([
  "ADDRESS", "ARTICLE", "DIV", "DL", "DT", "DD", "H1", "H2", "H3", "H4",
  "H5", "H6", "LI", "OL", "P", "SECTION", "TABLE", "TR", "UL",
]);

function safeEmailLink(value: string | null): string | null {
  if (!value) return null;
  try {
    const parsed = new URL(value);
    return ["http:", "https:", "mailto:"].includes(parsed.protocol) ? value : null;
  } catch {
    return null;
  }
}

function normalizeReadableEmailText(value: string): string {
  return value
    .replace(/\u00a0/g, " ")
    .replace(/\r\n?/g, "\n")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function readableHtmlFallback(source: string): string {
  return normalizeReadableEmailText(
    source
      .replace(/<!--[\s\S]*?-->/g, "")
      .replace(/<script\b[^>]*>[\s\S]*?<\/script\s*>/gi, "")
      .replace(/<style\b[^>]*>[\s\S]*?<\/style\s*>/gi, "")
      .replace(/<br\b[^>]*\/?>/gi, "\n")
      .replace(/<li\b[^>]*>/gi, "\n• ")
      .replace(/<\/(?:p|div|blockquote|li|tr|h[1-6])\s*>/gi, "\n")
      .replace(/<[^>]*>/g, "")
      .replace(/&nbsp;/gi, " ")
      .replace(/&amp;/gi, "&")
      .replace(/&quot;/gi, "\"")
      .replace(/&#39;|&apos;/gi, "'")
      .replace(/&gt;/gi, ">"),
  );
}

function htmlEmailToReadableText(source: string): string {
  if (!/<\s*[a-z][^>]*>/i.test(source)) return source;
  if (typeof DOMParser === "undefined") return readableHtmlFallback(source);

  const document = new DOMParser().parseFromString(source, "text/html");
  document.querySelectorAll("script,style,iframe,object,embed,form,svg,math").forEach((node) => {
    node.remove();
  });

  const visit = (node: Node): string => {
    if (node.nodeType === 3) return node.textContent || "";
    if (node.nodeType !== 1) return "";
    const element = node as HTMLElement;
    const tag = element.tagName;
    if (["SCRIPT", "STYLE", "IFRAME", "OBJECT", "EMBED", "FORM", "SVG", "MATH"].includes(tag)) {
      return "";
    }
    if (tag === "BR") return "\n";
    if (tag === "IMG") {
      const alt = element.getAttribute("alt")?.trim();
      return alt ? `[Image: ${alt}]` : "";
    }
    const content = Array.from(element.childNodes, visit).join("");
    if (tag === "A") {
      const href = safeEmailLink(element.getAttribute("href"));
      return href && !content.includes(href) ? `${content} (${href})` : content;
    }
    if (tag === "LI") return `\n• ${content.trim()}\n`;
    if (tag === "BLOCKQUOTE") {
      return `\n${content.split("\n").map((line) => line ? `> ${line}` : "").join("\n")}\n`;
    }
    return EMAIL_BLOCK_TAGS.has(tag) ? `\n${content}\n` : content;
  };
  return normalizeReadableEmailText(visit(document.body));
}

const FOOTER_NOTICE_PATTERNS = [
  /\bconfidential(?:ity)?\b/i,
  /\bprivileged(?: and confidential)?\b/i,
  /\bintended recipient\b/i,
  /\bif you (?:are not|aren't) the intended\b/i,
  /\breceived this (?:email|message) in error\b/i,
  /\bdo not (?:copy|distribute|forward|share)\b/i,
  /\bexternal sender\b/i,
  /\bexternal (?:email|message)\b/i,
  /\bsecurity (?:notice|warning|gateway)\b/i,
  /\boutside (?:of )?(?:our|your|the) organization\b/i,
  /\boriginated outside\b/i,
  /\bthis (?:email|message) (?:came|was sent) from outside\b/i,
  /\buse caution\b.{0,80}\b(?:link|attachment|sender)\b/i,
  /\bunsubscribe\b/i,
  /\bmanage (?:your )?(?:email )?preferences\b/i,
  /\bview (?:this )?email in (?:your )?browser\b/i,
  /\bclick here to (?:unsubscribe|opt out)\b/i,
  /\btracking (?:pixel|link|notice)\b/i,
  /\bemail (?:tracking|security gateway)\b/i,
  /\bthis message was sent automatically\b/i,
];

function isLongSignature(block: string): boolean {
  const lines = block.split("\n").map((line) => line.trim()).filter(Boolean);
  if (lines.length < 4) return false;
  if (/^(?:--\s*|best(?: regards| wishes)?[,!]?$|kind regards[,!]?$|warm regards[,!]?$|regards[,!]?$|sincerely[,!]?$|thank you[,!]?$|thanks[,!]?$)/i.test(lines[0])) {
    return true;
  }
  const contactLines = lines.filter((line) =>
    /@|https?:\/\/|\b(?:phone|mobile|tel|linkedin|director|founder|office)\b/i.test(line),
  );
  return lines.length >= 5 && contactLines.length >= 3;
}

function isQuotedBlock(block: string): boolean {
  const lines = block.split("\n").map((line) => line.trim()).filter(Boolean);
  if (!lines.length) return false;
  if (lines.every((line) => /^(?:>+|\|)\s?/.test(line))) return true;
  return (
    /^(?:on .{1,160}\bwrote:|-{3,}\s*(?:original message|forwarded message)|begin forwarded message)/i.test(lines[0])
    && lines.slice(1).some((line) => /^(?:>+|\|)\s?/.test(line) || /^(?:from|to|date|subject):/i.test(line))
  );
}

function normalizedBlockKey(block: string): string {
  return block.toLocaleLowerCase().replace(/\s+/g, " ").trim();
}

function uniqueBlocks(blocks: string[]): string[] {
  const seen = new Set<string>();
  return blocks.filter((block) => {
    const key = normalizedBlockKey(block);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function splitGmailBodyForDisplay(source: string): GmailBodyDisplaySections {
  const readable = normalizeReadableEmailText(htmlEmailToReadableText(source));
  const blocks = readable.split(/\n\s*\n+/).map((block) => block.trim()).filter(Boolean);
  const primary: string[] = [];
  const quoted: string[] = [];
  const footers: string[] = [];

  for (const block of blocks) {
    if (FOOTER_NOTICE_PATTERNS.some((pattern) => pattern.test(block)) || isLongSignature(block)) {
      footers.push(block);
    } else if (isQuotedBlock(block)) {
      quoted.push(block);
    } else {
      primary.push(block);
    }
  }

  return {
    primary: primary.join("\n\n"),
    quoted: uniqueBlocks(quoted),
    footers: uniqueBlocks(footers),
  };
}
