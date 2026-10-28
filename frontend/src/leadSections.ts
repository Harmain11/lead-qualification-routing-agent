export const leadDetailSections = [
  { id: "overview", label: "Overview" },
  { id: "ai", label: "Qualification" },
  { id: "follow-ups", label: "Follow up" },
  { id: "email", label: "Emails" },
  { id: "calls", label: "Calls" },
  { id: "deal", label: "Deal" },
  { id: "history", label: "Activity" },
  { id: "agent", label: "Ask Corral" },
] as const;

export type LeadDetailSection = (typeof leadDetailSections)[number]["id"] | "closing";

export function parseLeadSection(value: string | null): LeadDetailSection {
  if (value === "closing") return "deal";
  return leadDetailSections.find((section) => section.id === value)?.id ?? "overview";
}

export function leadSectionHref(leadId: string, section: LeadDetailSection): string {
  const url = new URL(window.location.href);
  url.searchParams.set("lead", leadId);
  url.searchParams.set("lead_tab", section);
  url.searchParams.delete("integration");
  return `${url.pathname}${url.search}${url.hash}`;
}
