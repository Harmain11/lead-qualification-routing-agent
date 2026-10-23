import type { Lead } from "../types";
import { NewLeadDot } from "./NewLeadDot";

export function LeadRowIdentity({
  lead,
  isNew = false,
}: {
  lead: Pick<Lead, "full_name" | "company_name">;
  isNew?: boolean;
}) {
  return <span className="lead-row-identity">
    <b className="lead-row-name">{isNew && <NewLeadDot />}{lead.full_name}</b>
    <span>{lead.company_name}</span>
  </span>;
}
