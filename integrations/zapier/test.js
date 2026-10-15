const test = require("node:test");
const assert = require("node:assert/strict");
const intake = require("./intake");
const auth = {base_url: "https://corral.example", intake_token: "a".repeat(43)};
test("connection URL rejects unsafe or ambiguous credentials", () => {
  assert.equal(intake.endpoint(auth), "https://corral.example/v1/intake/public/" + auth.intake_token);
  for (const base_url of ["http://corral.example", "https://user:pass@corral.example",
    "https://corral.example/elsewhere", "https://corral.example?other=1"]) {
    assert.throws(() => intake.endpoint({...auth, base_url}));
  }
  assert.throws(() => intake.endpoint({...auth, intake_token: "../workspace"}));
});
test("create forwards only approved inputs and preserves idempotency key", async () => {
  let captured;
  const z = {request: async request => {captured = request;
    return {throwForStatus() {}, json: {id: "lead-1"}};}};
  const result = await intake.create(z, {authData: auth,
    inputData: {external_id: "source-1", full_name: "Alex", workspace_id: "other", budget: 1000}});
  assert.equal(result.id, "lead-1");
  assert.equal(captured.body.external_id, "source-1");
  assert.equal(captured.body.budget, 1000);
  assert.equal(captured.body.source_detail, "zapier");
  assert.equal(captured.body.workspace_id, undefined);
  assert.equal(captured.method, "POST");
});
test("connection validation reads no leads", async () => {
  const result = await intake.connection({request: async request => {
    assert.ok(request.url.endsWith("/connection")); assert.equal(request.method, "GET");
    return {throwForStatus() {}, json: {connected: true, workspace_name: "Corral"}};}},
    {authData: auth});
  assert.equal(result.connected, true);
});
test("native package passes the official SDK schema", () => {
  const tools = require("zapier-platform-core/src/tools/schema");
  assert.deepEqual(tools.validateApp(require("./index")), []);
});
