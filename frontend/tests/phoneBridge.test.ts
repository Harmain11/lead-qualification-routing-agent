import assert from "node:assert/strict";
import test from "node:test";
import { CLOUDTALK_ORIGIN, cloudTalkEvents, readCloudTalkEvent } from "../src/components/phoneBridge.ts";

test("CloudTalk call state accepts only the provider origin and mounted frame", () => {
  const frame = {};
  const event = { origin: CLOUDTALK_ORIGIN, source: frame, data: { event: "calling" } };
  assert.equal(readCloudTalkEvent(event, frame), "calling");
  assert.equal(readCloudTalkEvent({ ...event, origin: "https://phone.cloudtalk.io.evil.test" }, frame), null);
  assert.equal(readCloudTalkEvent({ ...event, source: {} }, frame), null);
  assert.equal(readCloudTalkEvent(event, null), null);
});

test("CloudTalk bridge handles strings, malformed messages and terminal call state", () => {
  const frame = {};
  const read = (data: unknown) => readCloudTalkEvent({ origin: CLOUDTALK_ORIGIN, source: frame, data }, frame);
  assert.equal(read('{"event":"ended"}'), "ended");
  for (const payload of [null, [], "invalid", { event: "unknown" }, { event: "__proto__" }, { event: 3 }]) assert.equal(read(payload), null);
  assert.equal(cloudTalkEvents.hangup.active, true);
  assert.equal(cloudTalkEvents.ended.active, false);
});
