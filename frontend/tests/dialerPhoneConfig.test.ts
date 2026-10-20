import assert from "node:assert/strict";
import test from "node:test";

import {
  isDialerPhoneAvailable,
  isEmbeddedDialerConnection,
} from "../src/components/dialerPhoneConfig.ts";

const verifiedCloudTalk = {
  id: "connection-1",
  provider: "cloudtalk",
  account_name: "Sales team",
  status: "verified",
  is_active: true,
};

test("only an active, verified embedded dialer can show the phone dock", () => {
  assert.equal(isDialerPhoneAvailable(verifiedCloudTalk), true);
  assert.equal(
    isDialerPhoneAvailable({ ...verifiedCloudTalk, status: "connected" }),
    false,
  );
  assert.equal(
    isDialerPhoneAvailable({ ...verifiedCloudTalk, is_active: false }),
    false,
  );
  assert.equal(
    isDialerPhoneAvailable({ ...verifiedCloudTalk, provider: "twilio" }),
    false,
  );
  assert.equal(isDialerPhoneAvailable(null), false);
  assert.equal(isEmbeddedDialerConnection({ provider: "aircall" }), true);
});
