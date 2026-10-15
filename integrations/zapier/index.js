const intake = require("./intake");
module.exports = {
  version: require("./package.json").version,
  platformVersion: require("zapier-platform-core").version,
  authentication: {
    type: "custom",
    fields: [
      {key: "base_url", label: "Corral HTTPS origin", required: true, isNoSecret: true,
        helpText: "Example: https://corral.yourcompany.com (no trailing path)."},
      {key: "intake_token", type: "password", label: "Workspace intake token",
        required: true, isNoSecret: false,
        helpText: "Last segment of the public intake URL in Corral Integrations. Rotating it disconnects this app."}
    ],
    test: intake.connection,
    connectionLabel: "{{workspace_name}}"
  },
  creates: {create_lead: {
    key: "create_lead", noun: "Lead",
    display: {label: "Create Lead", description: "Create and qualify an inbound lead in a connected Corral workspace."},
    operation: {
      perform: intake.create,
      inputFields: intake.fields.map(key => ({
        key, label: key.replaceAll("_", " "), required: ["external_id", "full_name", "email",
          "company_name", "product_or_service", "message"].includes(key),
        type: key === "employee_count" ? "integer" : key === "budget" ? "number" : "string",
        ...(key === "external_id" ? {helpText: "Stable source record ID. Retries reuse this ID to avoid duplicate leads."} : {})
      })),
      sample: {id: "sample-lead", full_name: "Alex Smith", email: "alex@example.com",
        company_name: "Acme", source: "website", source_detail: "zapier"},
      outputFields: ["id", "full_name", "email", "company_name", "source", "source_detail"]
        .map(key => ({key, type: "string"}))
    }
  }}
};
