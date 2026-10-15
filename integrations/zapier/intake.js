function endpoint(auth) {
  let url;
  try { url = new URL(auth.base_url); } catch { throw new Error("Enter a valid HTTPS Corral origin."); }
  if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash ||
      url.pathname !== "/" || (url.port && url.port !== "443") ||
      !/^[A-Za-z0-9_-]{32,128}$/.test(auth.intake_token || "")) {
    throw new Error("Use an HTTPS Corral origin and the intake token from Integrations.");
  }
  return url.origin + "/v1/intake/public/" + auth.intake_token;
}
const fields = ["external_id", "full_name", "email", "company_name", "product_or_service",
  "message", "phone", "job_title", "industry", "geography", "budget", "employee_count"];
async function connection(z, bundle) {
  const response = await z.request({url: endpoint(bundle.authData) + "/connection", method: "GET"});
  response.throwForStatus();
  return response.json;
}
async function create(z, bundle) {
  const body = Object.fromEntries(fields.filter(key => bundle.inputData[key] !== undefined)
    .map(key => [key, bundle.inputData[key]]));
  body.source_detail = "zapier";
  const response = await z.request({url: endpoint(bundle.authData), method: "POST", body,
    headers: {"Content-Type": "application/json"}});
  response.throwForStatus();
  return response.json;
}
module.exports = {endpoint, fields, connection, create};
