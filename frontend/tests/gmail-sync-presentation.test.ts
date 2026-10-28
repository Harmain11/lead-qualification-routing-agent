import assert from "node:assert/strict";
import { test } from "node:test";
import { parseGmailReviewNotes } from "../src/gmailSyncPresentation.ts";

test("formats Gmail participant review output without thread IDs or machine reason codes", () => {
  const parsed = parseGmailReviewNotes(
    "Participant review: Thread 1a153a651dd3969 skipped: system_or_own_message. | "
      + "Thread 1a153b2e1d122517 selected Kathi Sitek <ksitek@example.test> as direct prospect; "
      + "Responded to commercial outreach with a proposed meeting time; updated. | "
      + "Thread 1a153b2e1d122518 skipped: no_supported_prospect_candidate: Ms. Patel: inquiry_evidence_not_in_source.",
  );

  assert.equal(parsed.general, "");
  assert.deepEqual(parsed.notes.map(({ outcome, title }) => ({ outcome, title })), [
    { outcome: "skipped", title: "Internal or system message" },
    { outcome: "selected", title: "Lead identified · Kathi Sitek" },
    { outcome: "skipped", title: "No supported prospect found" },
  ]);
  assert.equal(parsed.notes[1].detail, "Responded to commercial outreach with a proposed meeting time");
  assert.equal(parsed.notes[2].detail, "Ms. Patel: inquiry evidence not in source.");
  assert.ok(parsed.notes.every((note) => !/1a153a|1a153b|ksitek@example|_/.test(`${note.title} ${note.detail}`)));
});

test("keeps a non-participant sync error available as a readable summary", () => {
  assert.deepEqual(parseGmailReviewNotes("OpenAI credentials are unavailable."), {
    notes: [],
    general: "OpenAI credentials are unavailable.",
  });
});
