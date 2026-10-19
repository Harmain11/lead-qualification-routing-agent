import type { Lead } from "../types";
import type { LeadDetailSection } from "../leadSections";
import { LeadRowIdentity } from "./LeadRowIdentity";

export const leadSubpageCopy: Record<LeadDetailSection, { title: string; description: string }> = {
  overview: { title: "Lead overview", description: "Search and manage your workspace’s leads, then open a lead to inspect its complete record." },
  ai: { title: "AI & qualification", description: "Review recorded qualification scores and classifications. Open a lead’s AI assessment to inspect its evidence and reasoning." },
  "follow-ups": { title: "Follow-ups & meetings", description: "Choose a lead to manage its email follow-ups, meeting requests and scheduling." },
  email: { title: "Lead email threads", description: "Open a lead to review its Gmail conversation or compose a message from a connected mailbox." },
  closing: { title: "Comments & closing", description: "Review commercial outcomes and credited members. Open a lead to add comments or record a Project or Sale." },
  history: { title: "Lead history", description: "Review intake and delivery status, then open a lead’s full processing and interaction history." },
};
const readable = (value?: string | null) => value ? value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()) : "Not recorded";
const date = (value?: string | null) => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleDateString() : "Not recorded";

export function LeadSubpage({ section, rows, selectedIds, onToggleSelected, onInspect, loading }: {
  section: LeadDetailSection;
  rows: Lead[];
  selectedIds: Set<string>;
  onToggleSelected: (id: string) => void;
  onInspect: (id: string) => void;
  loading: boolean;
}) {
  const headings = section === "email" ? ["Contact email", "Company", "Last received"]
    : section === "ai" ? ["Score", "Classification", "Service"]
    : section === "follow-ups" ? ["Contact email", "Intake confirmation", "Received"]
      : section === "closing" ? ["Pipeline stage", "Recorded revenue", "Credited member"]
        : ["Received", "Intake confirmation", "CRM delivery"];
  const values = (lead: Lead) => section === "email"
    ? [lead.email || "Not recorded", lead.company_name || "Not recorded", date(lead.received_at)]
    : section === "ai"
    ? [lead.qualification ? `${lead.qualification.total_score}/100` : "Not scored", readable(lead.qualification?.classification), lead.product_or_service || "Not recorded"]
    : section === "follow-ups" ? [lead.email || "Not recorded", readable(lead.confirmation_email?.status), date(lead.received_at)]
      : section === "closing" ? [readable(lead.outcome?.pipeline_stage), lead.outcome?.closed_revenue != null ? `${lead.outcome.currency} ${lead.outcome.closed_revenue.toLocaleString()}` : "Not recorded", lead.outcome?.credited_name || "Not recorded"]
        : [date(lead.received_at), readable(lead.confirmation_email?.status), readable(lead.crm_sync?.status)];
  return <div className="lead-table-wrap lead-subpage-results" data-lead-subpage={section} aria-busy={loading} role="region" aria-label="Lead results table; scroll horizontally to view every column" tabIndex={0}>
    {loading && rows.length === 0 && <div className="table-state" role="status">Loading workspace leads…</div>}
    {!loading && rows.length === 0 && <div className="table-state lead-results-empty" role="status"><strong>No leads match the current filters</strong><span>Try a different search or clear the filters to see more leads.</span></div>}
    {rows.length > 0 && <table className="lead-table lead-subpage-table">
      <thead><tr><th aria-label="Select leads">Select</th><th>Lead</th>{headings.map((heading) => <th key={heading}>{heading}</th>)}<th>Open details</th></tr></thead>
      <tbody>{rows.map((lead) => <tr key={lead.id} tabIndex={0}
        onClick={(event) => { if (!(event.target as HTMLElement).closest("button")) onInspect(lead.id); }}
        onKeyDown={(event) => {
          if (event.target === event.currentTarget && (event.key === "Enter" || event.key === " ")) {
            event.preventDefault();
            onInspect(lead.id);
          }
        }}>
        <td className="lead-select-cell"><input type="checkbox" aria-label={`Select ${lead.full_name || "lead"}`} checked={selectedIds.has(lead.id)} onChange={() => onToggleSelected(lead.id)} onClick={(event) => event.stopPropagation()} /></td>
        <td className="lead-identity-cell"><button className="lead-subpage-name" onClick={() => onInspect(lead.id)}><LeadRowIdentity lead={lead} /></button></td>
        {values(lead).map((value, index) => <td key={headings[index]} data-label={headings[index]}>{value}</td>)}
        <td><button className="secondary-button" onClick={() => onInspect(lead.id)}>Open {section === "ai" ? "assessment" : section === "follow-ups" ? "follow-ups" : section === "closing" ? "comments & closing" : "history"}</button></td>
      </tr>)}</tbody>
    </table>}
  </div>;
}
