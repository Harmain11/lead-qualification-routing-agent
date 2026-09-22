import type { AgentActivity, Lead } from "../types";

export type Stage = {
  id: string;
  label: string;
  shortLabel: string;
  lane: "Inbound" | "AI Processing" | "Decision" | "Destinations";
  position: [number, number, number];
  screen: [number, number];
  detail: string;
};

const humanize = (value: string) =>
  value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());

export const stages: Stage[] = [
  { id: "website", label: "Website", shortLabel: "Website", lane: "Inbound", position: [-5.5, 2.2, -.7], screen: [6, 20], detail: "Inbound web signal" },
  { id: "email", label: "Email", shortLabel: "Email", lane: "Inbound", position: [-5.7, .7, .35], screen: [5, 38], detail: "Mailbox inquiry" },
  { id: "linkedin", label: "LinkedIn", shortLabel: "LinkedIn", lane: "Inbound", position: [-5.4, -.8, -.2], screen: [6, 57], detail: "Official lead input" },
  { id: "clutch", label: "Clutch", shortLabel: "Clutch", lane: "Inbound", position: [-5.1, -2.15, .7], screen: [8, 74], detail: "Marketplace inquiry" },
  { id: "normalize", label: "Normalize", shortLabel: "Normalize", lane: "AI Processing", position: [-3.25, 0, 0], screen: [22, 48], detail: "Canonical lead contract" },
  { id: "context", label: "Context", shortLabel: "Context", lane: "AI Processing", position: [-1.35, 1.15, .4], screen: [36, 34], detail: "Intent, evidence and uncertainty" },
  { id: "icp", label: "ICP", shortLabel: "ICP", lane: "AI Processing", position: [.35, .05, -.25], screen: [48, 47], detail: "Grounded customer fit" },
  { id: "qualification", label: "Qualification", shortLabel: "Qualify", lane: "AI Processing", position: [1.8, 1.2, .5], screen: [59, 32], detail: "Explainable 40 / 35 / 25 score" },
  { id: "routing", label: "Routing", shortLabel: "Route", lane: "Decision", position: [3.15, .05, 0], screen: [69, 47], detail: "Queue, priority and representative" },
  { id: "actions", label: "Action Plan", shortLabel: "Action", lane: "Decision", position: [4.2, 1.35, -.25], screen: [77, 29], detail: "Evidence-backed next step" },
  { id: "salesforce", label: "Salesforce", shortLabel: "Salesforce", lane: "Destinations", position: [5.65, 2.15, .5], screen: [89, 19], detail: "CRM delivery" },
  { id: "hubspot", label: "HubSpot", shortLabel: "HubSpot", lane: "Destinations", position: [5.85, .75, -.45], screen: [90, 39], detail: "CRM delivery" },
  { id: "slack", label: "Slack", shortLabel: "Slack", lane: "Destinations", position: [5.7, -.75, .55], screen: [89, 58], detail: "Operational notification" },
  { id: "rep", label: "Sales Rep", shortLabel: "Sales Rep", lane: "Destinations", position: [5.3, -2.1, -.35], screen: [87, 75], detail: "Human action" },
];

export const stageById = new Map(stages.map((stage) => [stage.id, stage]));

export const sourceStage = (source?: string) => {
  const normalized = source?.toLowerCase() ?? "website";
  if (normalized.includes("mail")) return "email";
  if (normalized.includes("linkedin")) return "linkedin";
  if (normalized.includes("clutch") || normalized.includes("fourth")) return "clutch";
  return "website";
};

export function journeyEdges(lead: Lead | null): Array<[string, string]> {
  if (!lead) return [];
  const result: Array<[string, string]> = [[sourceStage(lead.source), "normalize"]];
  if (lead.analysis) result.push(["normalize", "context"], ["context", "icp"]);
  if (lead.qualification) result.push(["icp", "qualification"]);
  if (lead.routing) result.push(["qualification", "routing"], ["routing", "actions"], ["routing", "rep"]);
  if (lead.routing?.notifications.length) result.push(["routing", "slack"]);
  if (lead.crm_sync) result.push(["actions", lead.crm_sync.provider === "hubspot" ? "hubspot" : "salesforce"]);
  return result;
}

export function fullActiveStages(lead: Lead | null) {
  const ids = new Set<string>();
  for (const [from, to] of journeyEdges(lead)) {
    ids.add(from);
    ids.add(to);
  }
  return ids;
}

export function stageFacts(stage: Stage, lead: Lead | null): Array<[string, string]> {
  if (!lead) return [["State", "Waiting for workspace lead data"], ["Capability", stage.detail]];
  if (stage.id === "qualification") return [
    ["ICP score", `${lead.qualification?.component_scores.icp_fit ?? 0} / 40`],
    ["Intent score", `${lead.qualification?.component_scores.intent_urgency ?? 0} / 35`],
    ["Engagement score", `${lead.qualification?.component_scores.engagement_quality ?? 0} / 25`],
    ["Total / band", `${lead.qualification?.total_score ?? 0} · ${humanize(lead.qualification?.classification ?? "Pending")}`],
    ["Rationale", lead.qualification?.rationale.slice(0, 2).join(" ") || "Awaiting qualification evidence"],
  ];
  if (stage.id === "routing" || stage.id === "actions" || stage.id === "rep") return [
    ["Queue", humanize(lead.routing?.assigned_queue ?? "Pending")],
    ["Representative", lead.routing?.assigned_rep_name ?? "Unassigned"],
    ["Priority", humanize(lead.routing?.priority ?? "Not assigned")],
    ["Action plan", lead.routing?.action_plan.immediate_action ?? "Pending"],
    ["Routing basis", lead.routing?.routing_reason ?? "Awaiting route"],
  ];
  if (["salesforce", "hubspot"].includes(stage.id)) return [
    ["Provider", lead.crm_sync?.provider ?? "Not connected"],
    ["Sync state", humanize(lead.crm_sync?.status ?? "Not synced")],
    ["Provider record", lead.crm_sync?.provider_entity_ids.contact_id ?? "Not available"],
  ];
  if (stage.id === "slack") return [
    ["Notification", humanize(lead.routing?.notification_state ?? "Pending")],
    ["Priority", humanize(lead.routing?.priority ?? "Not assigned")],
    ["Attempts", String(lead.routing?.notifications[0]?.attempts ?? 0)],
  ];
  if (stage.id === "context" || stage.id === "icp") return [
    ["Intent", humanize(lead.analysis?.intent_level ?? "Unknown")],
    ["Confidence", `${Math.round((lead.analysis?.overall_confidence ?? 0) * 100)}%`],
    ["Need", lead.analysis?.summary.need ?? lead.product_or_service],
    ["Evidence", lead.analysis?.summary.buying_signal ?? "No explicit signal"],
  ];
  if (stage.id === "normalize") return [
    ["Lead", `${lead.full_name} · ${lead.company_name}`],
    ["Contract", lead.normalization_version],
    ["Received", new Date(lead.received_at).toLocaleString()],
  ];
  return [["Lead", `${lead.full_name} · ${lead.company_name}`], ["Source", humanize(lead.source)], ["Signal", lead.message]];
}

export function latestActivityLabel(activity: AgentActivity | null) {
  const event = activity?.activity[0];
  return event ? `${humanize(event.stage)} · ${humanize(event.status)}` : "Awaiting audited activity";
}
