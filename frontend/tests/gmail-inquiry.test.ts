import assert from "node:assert/strict";
import { test } from "node:test";
import { cleanGmailInquiryText, parseGmailInquiry } from "../src/gmailInquiry.ts";

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
