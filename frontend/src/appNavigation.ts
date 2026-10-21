export const appPages = ["dashboard", "leads", "email", "contacts", "pipeline", "agents", "analytics", "integrations", "dialer", "team", "review", "settings", "icp", "accounts", "intelligence", "automations", "policies", "compliance"] as const;
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
    { id: "qualification-mix", label: "Qualification mix" },
    { id: "revenue-by-representative", label: "Revenue by representative" },
  ],
  agents: [
    { id: "overview", label: "Overview" },
    { id: "intelligence", label: "AI Research & Coaching", view: "intelligence", permission: "agents.view" },
    { id: "automations", label: "Workflows & Automations", view: "automations", permission: "agents.view" },
  ],
  integrations: [
    { id: "ai", label: "AI connections" },
    { id: "gmail", label: "Gmail" }, { id: "google_calendar", label: "Google Calendar" },
    { id: "slack", label: "Slack" }, { id: "salesforce", label: "Salesforce" },
    { id: "hubspot", label: "HubSpot" }, { id: "calendly", label: "Calendly" },
    { id: "outlook", label: "Outlook" }, { id: "gohighlevel", label: "GoHighLevel" },
    { id: "ai", label: "AI connection" },
    { id: "calling", label: "All dialers" },
    { id: "twilio", label: "Twilio" }, { id: "aircall", label: "Aircall" },
    { id: "ringcentral", label: "RingCentral" }, { id: "vapi", label: "Vapi" },
     { id: "retell", label: "Retell AI" }, { id: "cloudtalk", label: "CloudTalk" },
     { id: "linkedin", label: "LinkedIn" },
    { id: "instagram", label: "Instagram" }, { id: "messenger", label: "Messenger" },
    { id: "whatsapp_business", label: "WhatsApp Business" }, { id: "clutch", label: "Clutch" },
  ],
  dialer: [
    { id: "overview", label: "Overview" },
    { id: "history", label: "Call history" },
    { id: "analysis", label: "Transcript analysis" },
  ],
  settings: [
    { id: "company", label: "Company" },
    { id: "user", label: "User" },
  ],
};

const legacySettingsSections = new Set([
  "overview",
  "workspace",
  "routing-distribution",
  "website-lead-form",
  "email-templates",
  "users",
  "icp",
  "policies",
  "compliance",
]);

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
  if (page === "settings" && section && legacySettingsSections.has(section)) {
    return section;
  }
  return navigationSections[page]?.some((item) => item.id === section) ? section! : "";
}
