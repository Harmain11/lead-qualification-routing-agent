import assert from "node:assert/strict";
import { test } from "node:test";
import {
  cleanGmailInquiryText,
  parseGmailInquiry,
  splitGmailBodyForDisplay,
} from "../src/gmailInquiry.ts";

test("extracts Gmail thread metadata and presents forwarded content separately", () => {
  const inquiry = parseGmailInquiry(`Thread role: direct prospect. Referred by Dan Asif.

Original thread:
Thread role: direct prospect selected from parsed external participant headers.

--- Gmail message 1a153b264b135017 ---
References: <reference@gmail.com>
From: Dan Asif <dan.asif@ingenuity.example>
Date: Mon, 19 Oct 2026 15:25:50 +0500
Subject: Fwd: Rick can call you on Wednesday?
To: "workspace" <team@example.com>

Hi team, forwarding this inquiry.
---------- Forwarded message ----------
From: Rick Kosakowski <rick@example.test>
Date: Thu, 15 Oct 2026 20:21:00 +0500
Subject: Can we talk Wednesday?
To: Dan Asif <dan.asif@ingenuity.example>

Please contact me about the project.
`);

  assert.ok(inquiry);
  assert.equal(inquiry.role, "direct prospect");
  assert.equal(inquiry.context, "Referred by Dan Asif.");
  assert.equal(inquiry.messages.length, 1);
  assert.equal(inquiry.messages[0].from, "Dan Asif <dan.asif@ingenuity.example>");
  assert.equal(inquiry.messages[0].subject, "Fwd: Rick can call you on Wednesday?");
  assert.equal(inquiry.messages[0].body, "Hi team, forwarding this inquiry.");
  assert.equal(inquiry.messages[0].forwarded?.from, "Rick Kosakowski <rick@example.test>");
  assert.equal(inquiry.messages[0].forwarded?.subject, "Can we talk Wednesday?");
  assert.equal(inquiry.messages[0].forwarded?.body, "Please contact me about the project.");
});

test("leaves non-Gmail inquiry text available to the plain-message presentation", () => {
  assert.equal(parseGmailInquiry("Interested in learning more about your services."), null);
});

test("parses inline Gmail thread markers and compact header text", () => {
  const inquiry = parseGmailInquiry(`Thread role: direct prospect. Original thread: --- Gmail message abc123 ---
From: Pat Example <pat@example.test> To: team@example.test Subject: Pricing request Date: Tue, 20 Oct 2026 09:00:00 +0000

Please send pricing for the project.
`);
  assert.ok(inquiry);
  assert.equal(inquiry.role, "direct prospect");
  assert.equal(inquiry.messages.length, 1);
  assert.equal(inquiry.messages[0].from, "Pat Example <pat@example.test>");
  assert.equal(inquiry.messages[0].to, "team@example.test");
  assert.equal(inquiry.messages[0].subject, "Pricing request");
  assert.equal(inquiry.messages[0].body, "Please send pricing for the project.");
});

test("strips technical headers from the plain-text fallback", () => {
  const readable = cleanGmailInquiryText(`Thread role: direct prospect.
From: Pat Example <pat@example.test>
Subject: Pricing request
MIME-Version: 1.0

Please send pricing for the project.`);
  assert.equal(readable, "Please send pricing for the project.");
});

test("keeps a flattened Gmail thread focused on the newest lead reply", () => {
  const sections = splitGmailBodyForDisplay(`1a19da5c1f1ce9d1 --- From: Junaid <sales@example.test>
To: Harmain <harmain@example.test>
Subject: Re: Quick call

Here are three times that work for me.

1a19da5c1f1ce9d1 --- From: Harmain <harmain@example.test>
In-Reply-To: <old-message@example.test>
References: <old-message@example.test>
Subject: Re: Quick call

Wednesday works for me. Can we meet at 2 PM?`);

  assert.equal(sections.primary, "Wednesday works for me. Can we meet at 2 PM?");
  assert.equal(sections.quoted.length, 1);
  assert.match(sections.quoted[0], /Here are three times that work for me/);
  assert.doesNotMatch(sections.primary, /In-Reply-To|References|From:/);
});

test("parses a normal email's sender, recipients, date, subject, and body", () => {
  const inquiry = parseGmailInquiry(`From: Riley Chen <riley@example.test>
To: sales@example.test
Cc: operations@example.test
Date: Tue, 20 Oct 2026 09:00:00 +0000
Subject: Onboarding question

Hello,

Could we discuss your onboarding service?`);

  assert.ok(inquiry);
  assert.equal(inquiry.messages[0].from, "Riley Chen <riley@example.test>");
  assert.equal(inquiry.messages[0].to, "sales@example.test");
  assert.equal(inquiry.messages[0].cc, "operations@example.test");
  assert.equal(inquiry.messages[0].subject, "Onboarding question");
  assert.match(inquiry.messages[0].body, /Could we discuss your onboarding service/);
});

test("recovers an inquiry appended to a valid Date header", () => {
  const inquiry = parseGmailInquiry(`--- Gmail message flattened-date ---
From: Felicia Hodges <felicia@example.test>
To: junaid@example.test
Date: Fri, 16 Oct 2026 14:35:31 +0000 Hi Junaid, Noticed you follow a couple of data and marketing lists.
Subject: quick q`);

  assert.ok(inquiry);
  assert.equal(inquiry.messages[0].date, "Fri, 16 Oct 2026 14:35:31 +0000");
  assert.equal(inquiry.messages[0].subject, "quick q");
  assert.equal(
    inquiry.messages[0].body,
    "Hi Junaid, Noticed you follow a couple of data and marketing lists.",
  );
});

test("parses nested forwarded messages recursively without losing either body", () => {
  const inquiry = parseGmailInquiry(`--- Gmail message root ---
From: Maya Chen <maya@example.test>
To: sales@example.test
Subject: Original inquiry

Please review the forwarded thread.
---------- Forwarded message ----------
From: Evan Brooks <evan@example.test>
To: Maya Chen <maya@example.test>
Subject: Fwd: project notes

This is my note.
---------- Forwarded message ----------
From: Priya Nair <priya@example.test>
To: Evan Brooks <evan@example.test>
Subject: Project details

The project is ready to discuss.`);

  assert.ok(inquiry);
  const forwarded = inquiry.messages[0].forwarded;
  assert.ok(forwarded);
  assert.equal(forwarded.body, "This is my note.");
  assert.equal(forwarded.forwarded?.from, "Priya Nair <priya@example.test>");
  assert.equal(forwarded.forwarded?.body, "The project is ready to discuss.");
});

test("separates nested quoted text from the visible inquiry while preserving quote markers", () => {
  const sections = splitGmailBodyForDisplay(`Please send a few times to meet.

On Monday, Riley wrote:
> Can we review the plan?
> > The earlier goal was to reduce handoff delays.`);

  assert.equal(sections.primary, "Please send a few times to meet.");
  assert.equal(sections.quoted.length, 1);
  assert.match(sections.quoted[0], /> > The earlier goal/);
});

test("collapses duplicate disclaimer blocks and long signatures into one footer section", () => {
  const disclaimer = "This email is confidential and intended only for the named recipient.";
  const sections = splitGmailBodyForDisplay(`Could we schedule an onboarding call?

${disclaimer}

${disclaimer}

Best regards,
Riley Chen
Operations Director
riley@example.test
+1 415 555 0123`);

  assert.equal(sections.primary, "Could we schedule an onboarding call?");
  assert.equal(sections.footers.length, 2);
  assert.equal(sections.footers.filter((item) => item === disclaimer).length, 1);
  assert.match(sections.footers[1], /Operations Director/);
});

test("separates external-email warnings, opt-outs, and confidentiality notices from the visible body", () => {
  const sections = splitGmailBodyForDisplay(`The meeting is confirmed for Thursday at 2 PM.

WARNING: External email. This message came from outside the organization. Use caution with links and attachments.

Manage your email preferences or unsubscribe here.

This email is confidential and intended only for the named recipient.`);

  assert.equal(sections.primary, "The meeting is confirmed for Thursday at 2 PM.");
  assert.equal(sections.footers.length, 3);
  assert.match(sections.footers.join(" "), /External email/);
  assert.match(sections.footers.join(" "), /unsubscribe/i);
  assert.match(sections.footers.join(" "), /confidential/i);
});

test("extracts inline sender/security banners and deduplicates flat-text legal notices", () => {
  const sections = splitGmailBodyForDisplay(
    `You don't often get email from reese.maddox@ingenuity-systems.cloud. Learn why this is important [2]
    [EXTERNAL EMAIL] DMCU Security Advisory: This email was received from an external source. Do not open attachments, click on links, or trust the content from an unexpected source. If you are suspicious, contact I.T. Hello Kathi, I'm looking forward to a 10-minute Zoom call about a potential tech collaboration. Let me know a suitable time. Best Regards, Reese Maddox, Manager Customer Success. PS: If you want to opt out of our email list, reply with STOP.
    CONFIDENTIALITY NOTICE: This email is confidential and intended only for the named recipient. CONFIDENTIALITY NOTICE: This email is confidential and intended only for the named recipient. Links: ----- [1] https://aka.ms/security [2] https://aka.ms/sender-identification`,
  );

  assert.match(sections.primary, /Hello Kathi/);
  assert.match(sections.primary, /potential tech collaboration/);
  assert.match(sections.primary, /Best Regards/);
  assert.doesNotMatch(sections.primary, /You don't often get email|EXTERNAL EMAIL|Security Advisory|confidentiality notice|opt out|aka\.ms/i);
  assert.equal(sections.footers.filter((footer) => /CONFIDENTIALITY NOTICE/i.test(footer)).length, 1);
  assert.match(sections.footers.join(" "), /EXTERNAL EMAIL/);
  assert.match(sections.footers.join(" "), /opt out/);
  assert.match(sections.footers.join(" "), /aka\.ms/);
});

test("recovers a message body that was flattened onto the To line", () => {
  const inquiry = parseGmailInquiry(
    "From: Harmain Mughal <harmain@example.test> Date: Wed, 28 Oct 2026 15:28:00 +0500 Subject: AI agent To: junaid@ingenuity.example Hey, I want to ask you about making an AI automation for my business, need more details?",
  );
  assert.ok(inquiry);
  const [message] = inquiry.messages;
  assert.equal(message.to, "junaid@ingenuity.example");
  assert.equal(message.subject, "AI agent");
  assert.match(message.body, /^Hey, I want to ask you about making an AI automation/);
});

test("keeps display names and multiple recipients in address headers", () => {
  const inquiry = parseGmailInquiry(
    'From: Ana <ana@example.test>\nTo: "Sales Team" <sales@example.test>, ops@example.test\nSubject: Pricing\n\nCan you share pricing?',
  );
  assert.ok(inquiry);
  assert.equal(inquiry.messages[0].to, '"Sales Team" <sales@example.test>, ops@example.test');
  assert.equal(inquiry.messages[0].body, "Can you share pricing?");
});
