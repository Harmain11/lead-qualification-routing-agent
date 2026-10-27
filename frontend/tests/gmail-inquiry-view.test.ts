import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { after, before, test } from "node:test";

import react from "@vitejs/plugin-react";
import { createElement, type ComponentType } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer, type ViteDevServer } from "vite";

let vite: ViteDevServer | undefined;
let GmailInquiryView: ComponentType<{
  message: string;
  source?: string;
  leadEmail?: string;
  receivedAt?: string;
}>;

before(async () => {
  vite = await createServer({
    configFile: false,
    root: process.cwd(),
    plugins: [react()],
    server: { middlewareMode: true },
    appType: "custom",
  });
  const component = await vite.ssrLoadModule("/src/components/GmailInquiryView.tsx");
  GmailInquiryView = component.GmailInquiryView;
});

after(async () => {
  await vite?.close();
});

function sourceEmail(body: string, headers = "") {
  return `--- Gmail message ui-test ---
From: Riley Chen <riley@example.test>
To: sales@example.test
Cc: operations@example.test
Date: Tue, 20 Oct 2026 09:00:00 +0000
Subject: Onboarding question
${headers}

${body}`;
}

function render(message: string, source = "gmail") {
  assert.ok(GmailInquiryView, "Vite should load the real GmailInquiryView component");
  return renderToStaticMarkup(createElement(GmailInquiryView, {
    message,
    source,
    leadEmail: "riley@example.test",
    receivedAt: "2026-10-20T09:00:00Z",
  }));
}

test("renders normal email identity, direction, date, subject, body, and recipients", () => {
  const html = render(sourceEmail(`Hello team,

Could we discuss your onboarding service?

- Keep the current account owner
- Track agreed next steps`));

  assert.match(html, /Riley Chen/);
  assert.match(html, /riley@example\.test/);
  assert.match(html, /Received email/);
  assert.match(html, /Original inquiry/);
  assert.match(html, /Onboarding question/);
  assert.match(html, /<ul>/);
  assert.match(html, /sales@example\.test/);
  assert.match(html, /operations@example\.test/);
  assert.match(html, /View raw email/);
});

test("renders text recovered from a flattened Date header in the message body", () => {
  const html = render(`--- Gmail message flattened-date ---
From: Felicia Hodges <felicia@example.test>
To: sales@example.test
Date: Fri, 16 Oct 2026 14:35:31 +0000 Hi Junaid, Noticed you follow a couple of data and marketing lists.
Subject: quick q`);

  const time = html.match(/<time\b[^>]*>[\s\S]*?<\/time>/)?.[0] || "";
  assert.ok(time);
  assert.doesNotMatch(time, /Hi Junaid/);
  assert.ok(html.indexOf("quick q") < html.indexOf("Hi Junaid"));
  assert.doesNotMatch(html, /No message text was included/);
});

test("keeps forwarded messages under the collapsed quoted-content control", () => {
  const html = render(sourceEmail(`Could you review this note?

---------- Forwarded message ----------
From: Evan Brooks <evan@example.test>
To: Riley Chen <riley@example.test>
Date: Mon, 19 Oct 2026 14:30:00 +0000
Subject: Project details

We should discuss a short pilot.`));

  assert.match(html, /<summary>Quoted\/forwarded content<\/summary>/);
  assert.match(html, /Forwarded email/);
  assert.match(html, /Project details/);
  assert.match(html, /We should discuss a short pilot/);
});

test("places nested quoted replies in a separate, readable disclosure", () => {
  const html = render(sourceEmail(`Please send over a few times to meet.

On Monday, Riley wrote:
> Can we review the plan?
> > The earlier goal was to reduce handoff delays.`));

  assert.match(html, /<summary>Quoted\/forwarded content<\/summary>/);
  assert.match(html, /class="gmail-inquiry-quote"/);
  assert.match(html, /The earlier goal was to reduce handoff delays/);
  assert.match(html, /Please send over a few times to meet/);
});

test("deduplicates footer notices in the readable card while keeping all raw evidence", () => {
  const notice = "This email is confidential and intended only for the named recipient.";
  const source = sourceEmail(`Please send the onboarding outline.

${notice}

${notice}`);
  const html = render(source);
  const readableCard = html.slice(0, html.indexOf('class="gmail-inquiry-raw"'));

  assert.equal(readableCard.split(notice).length - 1, 1);
  assert.match(html, /Show footer and disclaimer/);
  assert.equal(html.split(notice).length - 1, 3);
});

test("formats a flat email and collapses inline security and confidentiality notices", () => {
  const body = `You don't often get email from reese.maddox@ingenuity-systems.cloud. Learn why this is important [2] [EXTERNAL EMAIL] DMCU Security Advisory: This email was received from an external source. Do not open attachments, click links, or trust the content. If you are suspicious, contact I.T. Hello Kathi, I'm looking forward to a 10-minute Zoom call about a potential tech collaboration. Let me know a suitable time. Best Regards, Reese Maddox, Manager Customer Success. PS: If you want to opt out of our email list, reply with STOP. CONFIDENTIALITY NOTICE: This email is confidential and intended only for the named recipient. CONFIDENTIALITY NOTICE: This email is confidential and intended only for the named recipient. Links: ----- [1] https://aka.ms/security [2] https://aka.ms/sender-identification`;
  const html = render(sourceEmail(body));
  const readableCard = html.slice(0, html.indexOf('class="gmail-inquiry-raw"'));
  const bodyStart = readableCard.indexOf('class="gmail-inquiry-body-content"');
  const footerStart = readableCard.indexOf('class="gmail-inquiry-disclosure');
  const primary = readableCard.slice(bodyStart, footerStart);
  const footer = readableCard.slice(footerStart);

  assert.match(primary, /Hello Kathi/);
  assert.match(primary, /potential tech collaboration/);
  assert.match(primary, /Best Regards/);
  assert.match(primary, /<\/p><p>Best Regards,<br\/>Reese Maddox/);
  assert.doesNotMatch(primary, /EXTERNAL EMAIL|Security Advisory|confidentiality notice|opt out|aka\.ms/i);
  assert.match(footer, /Show footer and disclaimer/);
  assert.equal(footer.split("CONFIDENTIALITY NOTICE").length - 1, 1);
  assert.doesNotMatch(readableCard, /No message text was included/);
});

test("recovers the inquiry when Gmail appends the message to a flat Subject header", () => {
  const message = `--- Gmail message flat-subject ---
From: Reese Maddox <reese.maddox@ingenuity-systems.cloud>
To: Kathi <kathi@example.test>
Date: Wed, 14 Oct 2026 11:50:00 +0000
Subject: Kathi Can I call you on Thursday? You don't often get email from reese.maddox@ingenuity-systems.cloud. Learn why this is important [2] [2] [EXTERNAL EMAIL] DMCU Security Advisory: This email was received from an external source. Do not open attachments, click links, or trust the content from an unexpected source. If you are suspicious, contact I.T. Hello Kathi, I'm looking forward to hopping on a 10-minute Zoom call sometime this week to discuss a potential tech collaboration between Pill Pouch and our AI solutions. Let me know of a suitable time to connect. Best Regards, Reese Maddox, Manager Customer Success Ingenuity Systems. PS: If you want to opt out of our email list, simply hit reply with STOP in the subject line. CONFIDENTIALITY NOTICE: The information contained in this email is privileged, confidential and intended only for the individuals to whom it is addressed. Then delete this email and any attachments. Any unauthorized use, distribution or copying of this information is strictly prohibited. CONFIDENTIALITY NOTICE: The information contained in this email is privileged, confidential and intended only for the individuals to whom it is addressed. Then delete this email and any attachments. Any unauthorized use, distribution or copying of this information is strictly prohibited. Links: ----- [1] https://aka.ms/o0ukef [2] https://aka.ms/LearnAboutSenderIdentification Links: ----- [1] https://aka.ms/o0ukef [2] https://aka.ms/LearnAboutSenderIdentification`;
  const html = render(message);
  const subject = html.match(/<div class="gmail-inquiry-subject">[\s\S]*?<\/div>/)?.[0] || "";
  const bodyStart = html.indexOf('class="gmail-inquiry-message-body"');
  const footerStart = html.indexOf('class="gmail-inquiry-disclosure gmail-inquiry-footer"');
  const rawStart = html.indexOf('class="gmail-inquiry-raw"');
  const primary = html.slice(bodyStart, footerStart);
  const footer = html.slice(footerStart, rawStart);

  assert.match(subject, /<h4>Kathi Can I call you on Thursday\?<\/h4>/);
  assert.doesNotMatch(subject, /You don't often get email|DMCU Security Advisory|EXTERNAL EMAIL/i);
  assert.match(primary, /Hello Kathi/);
  assert.match(primary, /potential tech collaboration/);
  assert.match(primary, /Best Regards/);
  assert.doesNotMatch(primary, /EXTERNAL EMAIL|Security Advisory|confidentiality notice|opt out|aka\.ms/i);
  assert.equal(footer.split("CONFIDENTIALITY NOTICE").length - 1, 1);
  assert.equal(footer.split("Links:").length - 1, 1);
  assert.doesNotMatch(html.slice(0, rawStart), /No message text was included/);
});

test("does not call a notice-only email empty while its footer details are available", () => {
  const html = render(sourceEmail("This email is confidential and intended only for the named recipient."));
  const readableCard = html.slice(0, html.indexOf('class="gmail-inquiry-raw"'));

  assert.match(readableCard, /Show footer and disclaimer/);
  assert.doesNotMatch(readableCard, /No message text was included/);
});

test("wraps long URLs as safe links in the message body", () => {
  const longUrl = `https://example.test/${"customer-onboarding/".repeat(14)}review`;
  const html = render(sourceEmail(`Reference: ${longUrl}`));

  assert.ok(html.includes(`href="${longUrl}"`));
  assert.match(html, /rel="noopener noreferrer" target="_blank"/);
});

test("renders HTML email content as safe text rather than injecting markup", () => {
  const html = render(sourceEmail(
    `<p>Hello team</p><p>Open the <a href="javascript:alert(1)">unsafe link</a>.</p><script>runUnsafeCode()</script>`,
  ));
  const readableCard = html.slice(0, html.indexOf('class="gmail-inquiry-raw"'));

  assert.match(readableCard, /Hello team/);
  assert.match(readableCard, /unsafe link/);
  assert.doesNotMatch(readableCard, /<script|runUnsafeCode/);
  assert.doesNotMatch(readableCard, /href="javascript:/);
  assert.match(html, /&lt;script&gt;runUnsafeCode\(\)&lt;\/script&gt;/);
});

test("keeps sender and date metadata readable at phone-width breakpoints", () => {
  const css = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");
  const mobileRules = css.match(/@media\s*\(max-width:\s*480px\)\s*\{([\s\S]*?)^\}/m)?.[1];

  assert.ok(mobileRules);
  assert.match(mobileRules, /\.gmail-inquiry-message-meta/);
  assert.match(mobileRules, /\.gmail-inquiry-badges/);
  assert.match(mobileRules, /\.gmail-inquiry-message-body/);
});
