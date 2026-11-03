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

const EMAIL_MONTHS: Record<string, number> = {
  jan: 1, january: 1,
  feb: 2, february: 2,
  mar: 3, march: 3,
  apr: 4, april: 4,
  may: 5,
  jun: 6, june: 6,
  jul: 7, july: 7,
  aug: 8, august: 8,
  sep: 9, sept: 9, september: 9,
  oct: 10, october: 10,
  nov: 11, november: 11,
  dec: 12, december: 12,
};

const RFC_EMAIL_DATE =
  /^(?:(?:Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|Thu(?:rsday)?|Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?),?\s+)?(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s+(Z|[+-]\d{4}|[+-]\d{2}:\d{2}|GMT|UTC|UT)$/i;

export function parseEmailDate(value: string): Date | null {
  const source = value.trim();
  if (!source) return null;

  const match = RFC_EMAIL_DATE.exec(source);
  if (match) {
    const day = Number(match[1]);
    const month = EMAIL_MONTHS[match[2].toLowerCase()];
    const year = Number(match[3]);
    const hour = Number(match[4]);
    const minute = Number(match[5]);
    const second = Number(match[6] || "0");
    const zone = match[7].toUpperCase();
    if (!month || hour > 23 || minute > 59 || second > 59) return null;

    const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
    const daysInMonth = [31, leapYear ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1];
    if (day < 1 || day > daysInMonth) return null;

    let isoZone = "Z";
    if (/^[+-]/.test(zone)) {
      const digits = zone.replace(":", "");
      const offsetHours = Number(digits.slice(1, 3));
      const offsetMinutes = Number(digits.slice(3, 5));
      if (offsetHours > 23 || offsetMinutes > 59) return null;
      isoZone = `${digits.slice(0, 3)}:${digits.slice(3, 5)}`;
    }
    const iso = `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}T${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}:${String(second).padStart(2, "0")}${isoZone}`;
    const parsed = new Date(iso);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }

  // Keep accepting ISO timestamps and other date forms from older stored emails.
  const parsed = new Date(source);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function normalizeHeaderBoundaries(source: string) {
  return source
    .replace(/\r\n?/g, "\n")
    .replace(new RegExp(`[\\t ]+(?=(?:${GMAIL_HEADER_NAMES}):\\s)`, "gi"), "\n");
}

/**
 * Some Gmail payloads flatten an attached RFC-822 message into the parent
 * text. Normalize its boundary before parsing so the attachment cannot become
 * the visible lead reply.
 */
function normalizeRawGmailThreadMarkers(source: string) {
  const header = `(?:${GMAIL_HEADER_NAMES})`;
  return source
    .replace(/\r\n?/g, "\n")
    .replace(
      new RegExp(`(^|\\n)\\s*---\\s*(?=${header}:\\s)`, "gim"),
      "$1--- Gmail message imported ---\n",
    )
    // Gmail's normal marker ends in "Gmail message <id> ---". The negative
    // lookbehind preserves that marker while repairing only a flattened raw
    // RFC-822 boundary such as "1a19... --- From:".
    .replace(
      new RegExp(`(?<!Gmail message )\\b[a-z0-9_-]{8,}\\s+---\\s*(?=${header}:\\s)`, "gim"),
      "--- Gmail message imported ---\n",
    );
}

function recoverBodyAppendedToDateHeader(value: string) {
  const greeting = /\s+(?=(?:hi|hello|hey|dear|good\s+(?:morning|afternoon|evening)|greetings|to whom it may concern)\b)/i.exec(value);
  if (!greeting || greeting.index === undefined) return null;

  const date = value.slice(0, greeting.index).trim();
  const body = value.slice(greeting.index).trim();
  if (!date || !body || !parseEmailDate(date)) return null;
  return { date, body };
}

const ADDRESS_TOKEN = /^\s*(?:(?:"[^"]*"|[^<>,@"\n]+?)\s*<[^<>\s@]+@[^<>\s]+>|<?[^\s,<>@]+@[^\s,<>]+>?)\s*(?:,\s*)?/;

/** Address headers hold only addresses; text after the last address is the start of the body. */
function recoverBodyAppendedToAddressHeader(value: string) {
  let rest = value;
  let consumed = "";
  for (let guard = 0; guard < 50; guard += 1) {
    const match = ADDRESS_TOKEN.exec(rest);
    if (!match || !match[0].trim()) break;
    consumed += match[0];
    rest = rest.slice(match[0].length);
  }
  const body = rest.trim();
  const addresses = consumed.trim().replace(/,\s*$/, "");
  if (!addresses || !body) return null;
  return { addresses, body };
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
  if (headers.subject) {
    const inlineBodyStart = /(?:you (?:don't|do not) often get email from\b|\[(?:external email|external message)\]|\bDMCU Security Advisory\s*:)/i
      .exec(headers.subject);
    if (inlineBodyStart && inlineBodyStart.index > 0) {
      const inlineBody = headers.subject.slice(inlineBodyStart.index).trim();
      headers.subject = headers.subject.slice(0, inlineBodyStart.index).trim();
      body = [inlineBody, body].filter(Boolean).join("\n");
    }
  }
  for (const name of ["to", "cc", "bcc", "from", "reply-to"]) {
    if (!headers[name]) continue;
    const recovered = recoverBodyAppendedToAddressHeader(headers[name]);
    if (recovered) {
      headers[name] = recovered.addresses;
      body = [recovered.body, body].filter(Boolean).join("\n").trim();
    }
  }
  if (headers.date) {
    const recovered = recoverBodyAppendedToDateHeader(headers.date);
    if (recovered) {
      headers.date = recovered.date;
      body = [recovered.body, body].filter(Boolean).join("\n").trim();
    }
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
  const source = normalizeRawGmailThreadMarkers(message)
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

function formatThreadMessage(message: GmailInquiryMessage): string {
  const headers = [
    message.from && `From: ${message.from}`,
    message.to && `To: ${message.to}`,
    message.subject && `Subject: ${message.subject}`,
    message.date && `Date: ${message.date}`,
  ].filter(Boolean);
  return [...headers, message.body].filter(Boolean).join("\n").trim();
}

function formatFlatEmailBody(source: string): string {
  return normalizeReadableEmailText(source)
    .replace(
      /\b((?:hi|hello|dear)(?:\s+[^,\n]{1,80})?[,!:])\s+(?=\S)/i,
      "$1\n\n",
    )
    .replace(
      /\s+((?:(?:best|kind|warm)\s+regards)|regards|sincerely|thank you|thanks)([,!:])/gi,
      (match, signOff: string, punctuation: string, offset: number, source: string) => {
        if (
          /^regards$/i.test(signOff)
          && /\b(?:best|kind|warm)\s+$/i.test(source.slice(0, offset))
        ) {
          return match;
        }
        return `\n\n${signOff}${punctuation}`;
      },
    )
    .replace(
      /((?:best|kind|warm)\s+regards|regards|sincerely|thank you|thanks)([,!:])\s+(?=\S)/gi,
      "$1$2\n",
    )
    .replace(/\s+(?=P\.?\s*S\.?:)/gi, "\n\n");
}

function splitInlineFooterNotices(source: string): { primary: string; footers: string[] } {
  const ranges: Array<{ start: number; end: number }> = [];
  const externalNotice = /\[(?:external email|external message)\]|\bDMCU Security Advisory\s*:/i.exec(source);
  const senderWarning = /\byou (?:don't|do not) often get email from\b/i.exec(source);

  if (externalNotice) {
    const senderWarningIsNearby = senderWarning
      && senderWarning.index < externalNotice.index
      && externalNotice.index - senderWarning.index < 360;
    const start = senderWarningIsNearby ? senderWarning.index : externalNotice.index;
    const remainder = source.slice(externalNotice.index + externalNotice[0].length);
    const greeting = /\b(?:hi|hello|dear)(?:\s+[^,\n]{1,80})?[,!:]/i.exec(remainder);
    const nextNotice = /\bCONFIDENTIALITY NOTICE\s*:|\b(?:P\.?\s*S\.?:?\s*)?If you want to opt out\b/i.exec(remainder);
    const end = greeting
      ? externalNotice.index + externalNotice[0].length + greeting.index
      : nextNotice
        ? externalNotice.index + externalNotice[0].length + nextNotice.index
        : source.length;
    if (end > start) ranges.push({ start, end });
  } else if (senderWarning) {
    const remainder = source.slice(senderWarning.index);
    const greeting = /\b(?:hi|hello|dear)(?:\s+[^,\n]{1,80})?[,!:]/i.exec(remainder);
    const learnMore = /\bLearn why this is important\b/i.exec(remainder);
    const end = greeting
      ? senderWarning.index + greeting.index
      : learnMore
        ? senderWarning.index + learnMore.index + learnMore[0].length
        : source.length;
    if (end > senderWarning.index) ranges.push({ start: senderWarning.index, end });
  }

  const confidentialityMarkers = Array.from(source.matchAll(/\bCONFIDENTIALITY NOTICE\s*:/gi));
  confidentialityMarkers.forEach((marker, index) => {
    const start = marker.index ?? 0;
    const next = confidentialityMarkers[index + 1];
    const links = /\bLinks?\s*:\s*-{2,}\s*\[\d+\]/i.exec(source.slice(start + marker[0].length));
    const end = next?.index
      ?? (links ? start + marker[0].length + links.index : source.length);
    if (end > start) ranges.push({ start, end });
  });

  const optOut = /\b(?:P\.?\s*S\.?:?\s*)?If you want to opt out\b/i.exec(source);
  if (optOut) {
    const next = confidentialityMarkers.find((marker) => (marker.index ?? 0) > optOut.index);
    const end = next?.index ?? source.length;
    if (end > optOut.index) ranges.push({ start: optOut.index, end });
  }

  const linksMarkers = Array.from(source.matchAll(/\bLinks?\s*:\s*-{2,}\s*\[\d+\]/gi));
  linksMarkers.forEach((marker, index) => {
    const start = marker.index ?? 0;
    const end = linksMarkers[index + 1]?.index ?? source.length;
    if (end > start) ranges.push({ start, end });
  });

  ranges.sort((left, right) => left.start - right.start);
  const merged: Array<{ start: number; end: number }> = [];
  for (const range of ranges) {
    const previous = merged.at(-1);
    if (previous && range.start < previous.end) {
      previous.end = Math.max(previous.end, range.end);
    } else {
      merged.push({ ...range });
    }
  }

  const primary: string[] = [];
  const footers: string[] = [];
  let cursor = 0;
  for (const range of merged) {
    const bodyText = formatFlatEmailBody(source.slice(cursor, range.start));
    const footerText = formatFlatEmailBody(source.slice(range.start, range.end));
    if (bodyText) primary.push(bodyText);
    if (footerText) footers.push(footerText);
    cursor = range.end;
  }
  const trailingBody = formatFlatEmailBody(source.slice(cursor));
  if (trailingBody) primary.push(trailingBody);
  return { primary: primary.join("\n\n"), footers };
}

export function splitGmailBodyForDisplay(source: string): GmailBodyDisplaySections {
  const readable = normalizeReadableEmailText(htmlEmailToReadableText(source));
  const parsed = parseGmailInquiry(readable);
  const latest = parsed?.messages.at(-1);
  // A parsed Gmail thread is rendered as the newest message plus collapsed
  // earlier messages. This keeps the prospect's current reply readable and
  // preserves the rest of the conversation without exposing RFC headers.
  const threadQuotes = parsed && parsed.messages.length > 1
    ? parsed.messages.slice(0, -1).map(formatThreadMessage).filter(Boolean)
    : [];
  const displayBody = latest?.body || readable;
  const inline = splitInlineFooterNotices(displayBody);
  const blocks = inline.primary.split(/\n\s*\n+/).map((block) => block.trim()).filter(Boolean);
  const primary: string[] = [];
  const quoted: string[] = [...threadQuotes];
  const footers: string[] = [...inline.footers];

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
