export const leadDetailSections = [
  { id: "overview", label: "Overview" },
  { id: "agent", label: "Ask Corral" },
  { id: "ai", label: "AI & qualification" },
  { id: "follow-ups", label: "Follow-ups & meetings" },
  { id: "email", label: "Email" },
  { id: "closing", label: "Comments & closing" },
  { id: "history", label: "History" },
] as const;

export type LeadDetailSection = (typeof leadDetailSections)[number]["id"];

export function parseLeadSection(value: string | null): LeadDetailSection {
  return leadDetailSections.find((section) => section.id === value)?.id ?? "overview";
}

export function leadSectionHref(leadId: string, section: LeadDetailSection): string {
  const url = new URL(window.location.href);
  url.searchParams.set("lead", leadId);
  url.searchParams.set("lead_tab", section);
  url.searchParams.delete("integration");
  return `${url.pathname}${url.search}${url.hash}`;
}
