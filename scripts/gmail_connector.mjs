// Server-only bridge: credentials and token refresh stay in the official SDK.
import { ReplitConnectors } from "@replit/connectors-sdk";

async function main() {
  let input = "";
  for await (const chunk of process.stdin) input += chunk;
  const request = JSON.parse(input);
  const connectors = new ReplitConnectors(); // Fresh client for every invocation.
  let path;
  let options;
  if (request.action === "profile") {
    path = "/gmail/v1/users/me/profile";
    options = { method: "GET" };
  } else if (request.action === "send" && typeof request.raw === "string") {
    path = "/gmail/v1/users/me/messages/send";
    options = {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ raw: request.raw }),
    };
  } else {
    throw new Error("Unsupported Gmail action");
  }
  const response = await connectors.proxy("google-mail", path, options);
  if (!response.ok) {
    // Never echo provider response bodies, credentials, or message contents.
    process.stdout.write(JSON.stringify({ ok: false, status: response.status }));
    process.exitCode = 1;
    return;
  }
  const body = await response.json();
  const result = request.action === "profile"
    ? { ok: true, emailAddress: body.emailAddress }
    : { ok: true, id: body.id };
  process.stdout.write(JSON.stringify(result));
}

main().catch(() => {
  process.stdout.write(JSON.stringify({ ok: false, error: "Gmail connector request failed" }));
  process.exitCode = 1;
});
