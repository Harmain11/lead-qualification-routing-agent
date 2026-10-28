import type { Lead } from "../types";

export function LeadRowIdentity({ lead }: { lead: Pick<Lead, "full_name" | "company_name"> }) {
  return <span className="lead-row-identity"><b>{lead.full_name}</b><span>{lead.company_name}</span></span>;
}
