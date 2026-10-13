export const appPages = ["dashboard", "leads", "contacts", "pipeline", "agents", "analytics", "integrations", "team", "review", "settings", "icp", "accounts", "intelligence", "automations", "policies", "compliance", "agency"] as const;
export type AppPage = typeof appPages[number];
export type NavigationSection = { id: string; label: string; permission?: string; view?: AppPage };

export const navigationSections: Partial<Record<AppPage, NavigationSection[]>> = {
  leads: [
    { id: "overview", label: "Overview" },
    { id: "ai", label: "AI & qualification" },
    { id: "follow-ups", label: "Follow-ups & meetings" },
    { id: "closing", label: "Comments & closing" },
    { id: "history", label: "History" },
  ],
  analytics: [
    { id: "channel-performance", label: "Source performance" },
    { id: "lead-trajectory", label: "Lead trajectory" },
    { id: "revenue-by-channel", label: "Revenue by source" },
    { id: "revenue-by-service", label: "Revenue by service" },
    { id: "representatives", label: "Member performance" },
    { id: "revenue-trajectory", label: "Revenue trajectory" },
  ],
  integrations: [
    { id: "gmail", label: "Gmail" }, { id: "google_calendar", label: "Google Calendar" },
    { id: "slack", label: "Slack" }, { id: "salesforce", label: "Salesforce" },
    { id: "hubspot", label: "HubSpot" }, { id: "calendly", label: "Calendly" },
    { id: "outlook", label: "Outlook" }, { id: "gohighlevel", label: "GoHighLevel" }, { id: "openai", label: "OpenAI" },
    { id: "twilio", label: "Twilio" }, { id: "aircall", label: "Aircall" },
    { id: "ringcentral", label: "RingCentral" }, { id: "vapi", label: "Vapi" },
    { id: "retell", label: "Retell AI" },
  ],
  settings: [
    { id: "workspace", label: "Workspace" },
    { id: "routing-distribution", label: "Routing distribution", permission: "routing.view" },
    { id: "website-lead-form", label: "Website lead form", permission: "integrations.manage" },
    { id: "email-templates", label: "Email templates", permission: "integrations.manage" },
    { id: "users", label: "Users & permissions", view: "team", permission: "members.view" },
    { id: "icp", label: "Ideal Customer Profile", view: "icp", permission: "icp.view" },
  ],
};

export function pageFromSearch(search: string): AppPage {
  const params = new URLSearchParams(search);
  if (/^[0-9a-f-]{36}$/i.test(params.get("lead") ?? "")) return "leads";
  const page = params.get("view");
  if (appPages.includes(page as AppPage)) return page as AppPage;
  return params.has("integration") ? "integrations" : "dashboard";
}

export function sectionFromSearch(search: string, page: AppPage): string {
  const params = new URLSearchParams(search);
  const section = page === "leads" ? params.get("lead_tab") : params.get("section");
  return navigationSections[page]?.some((item) => item.id === section) ? section! : "";
}
