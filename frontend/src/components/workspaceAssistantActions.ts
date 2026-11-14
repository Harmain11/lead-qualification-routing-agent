import {
  createIcpDraft,
  createLead,
  disconnectIntegration,
  inviteMember,
  reactivateMember,
  setLeadConfirmationAutomation,
  suspendMember,
  updateIcpDraft,
  updateMemberPermissions,
  updateMemberRole,
  updateRoutingConfig,
  updateLeadProfile,
  saveLeadNote,
  updateLeadProgress,
  reevaluateLead,
  completeLeadManualReview,
  type WorkspaceAssistantAction,
  type WorkspaceAssistantArea,
} from "../api";
import type { IcpConfiguration, Lead, LeadPayload } from "../types";
import { opsGet, opsSave, type Automation, type AutomationSpec, type PlaybookNode } from "../operationsApi";
import { createClientId } from "../clientIds";

export function applyWorkspaceEmailSignature(
  body: string,
  context: Record<string, unknown>,
): string {
  const workspaceProfile = context.workspace_profile as
    | { business_profile?: { email_signature?: unknown; business_name?: unknown } }
    | undefined;
  const profile = workspaceProfile?.business_profile;
  const placeholder = /^\s*(?:\[(?:your\s+)?(?:name|title|company(?:\s+name)?|email|phone)\]|\{\{\s*(?:sender|sender_name|your_name|signature)\s*\}\})\s*$/i;
  const signoff = /^\s*(?:best(?: regards)?|kind regards|warm regards|regards|sincerely|thanks|thank you)[,!:]?\s*$/i;
  const genericSender = /^\s*(?:the team|your team|sales team|the sales team)\s*$/i;
  let lines = body.split(/\r?\n/).filter((line) => !placeholder.test(line));
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    if (!signoff.test(lines[index])) continue;
    const suffix = lines.slice(index + 1).map((line) => line.trim()).filter(Boolean);
    if (suffix.every((line) => placeholder.test(line) || genericSender.test(line) || (line.length <= 160 && !/[.!?]/.test(line)))) {
      lines = lines.slice(0, index);
      break;
    }
  }
  while (lines.length && (!lines[lines.length - 1].trim() || genericSender.test(lines[lines.length - 1]) || placeholder.test(lines[lines.length - 1]))) {
    lines.pop();
  }
  const cleanBody = lines.join("\n").trim();
  const configuredSignature = typeof profile?.email_signature === "string"
    ? profile.email_signature.trim()
    : "";
  const businessName = typeof profile?.business_name === "string"
    ? profile.business_name.trim()
    : "";
  const signatureHasPlaceholder = configuredSignature
    .split(/\r?\n/)
    .some((line) => placeholder.test(line));
  const signature = signatureHasPlaceholder
    ? ""
    : configuredSignature || (businessName ? `Best regards,\n${businessName}` : "");
  return signature ? (cleanBody ? `${cleanBody}\n\n${signature}` : signature) : cleanBody;
}

function parsePayload(action: WorkspaceAssistantAction): Record<string, unknown> {
  if (!action.payload_json) throw new Error("The proposal is missing its reviewed values.");
  const parsed: unknown = JSON.parse(action.payload_json);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("The proposed values are not a valid object.");
  }
  return parsed as Record<string, unknown>;
}

export function workspaceNoteDraft(action: WorkspaceAssistantAction): string | null {
  if (action.kind !== "add_lead_note") return null;
  try {
    const payload = parsePayload(action);
    return typeof payload.note === "string" ? payload.note : "";
  } catch {
    return "";
  }
}

export function editWorkspaceNoteDraft(
  action: WorkspaceAssistantAction, note: string,
): WorkspaceAssistantAction {
  if (action.kind !== "add_lead_note") throw new Error("This proposal is not a note draft.");
  return { ...action, payload_json: JSON.stringify({ note }) };
}

export function leadCreationDraft(action: WorkspaceAssistantAction): Record<string, unknown> {
  if (action.kind !== "create_lead") throw new Error("This proposal is not a new lead draft.");
  return parsePayload(action);
}

export async function createWorkspaceLead(
  action: WorkspaceAssistantAction, permissions: ReadonlySet<string>,
): Promise<Lead> {
  requirePermission(permissions, "leads.edit");
  const { follow_up_note: _note, ...fields } = leadCreationDraft(action);
  const allowed = new Set([
    "full_name", "email", "phone", "company_name", "job_title", "industry",
    "employee_count", "annual_revenue", "geography", "budget", "product_or_service", "message",
  ]);
  if (Object.keys(fields).some((key) => !allowed.has(key))) {
    throw new Error("The new lead draft includes unsupported fields.");
  }
  if (!Object.values(fields).some((value) =>
    typeof value === "string" ? Boolean(value.trim()) : typeof value === "number" && value >= 0,
  )) throw new Error("Enter at least one known lead detail.");
  return createLead({
    ...fields as Partial<LeadPayload>,
    source: "manual", source_detail: "manual-control-center",
  });
}

const DRAFT_STEP_TYPES = new Set(["wait", "draft", "research", "coach", "note"]);

/**
 * Turns the agent's ordered step list into a paused, linear automation graph.
 * The result is always inactive: people review and activate it in Automations.
 */
export function automationDraftSpec(payload: Record<string, unknown>): AutomationSpec {
  const name = typeof payload.name === "string" ? payload.name.trim().slice(0, 120) : "";
  if (!name) throw new Error("Give the sequence or playbook a name.");
  const kind = payload.kind === "playbook" ? "playbook" : "sequence";
  const steps = Array.isArray(payload.steps) ? payload.steps : [];
  const body = steps.flatMap((step): PlaybookNode[] => {
    if (!step || typeof step !== "object") return [];
    const item = step as Record<string, unknown>;
    const type = typeof item.type === "string" ? item.type : "";
    if (!DRAFT_STEP_TYPES.has(type)) return [];
    const hours = Number(item.wait_hours);
    return [{
      id: "", type: type as PlaybookNode["type"], x: 60, y: 60, next: null, otherwise: null,
      wait_hours: Number.isFinite(hours) ? Math.min(2160, Math.max(1, Math.round(hours))) : 24,
      condition: "hot",
      text: typeof item.text === "string" ? item.text.slice(0, 2000) : "",
    }];
  }).slice(0, 48);
  if (!body.length) throw new Error("Add at least one step to the draft.");
  const chain: PlaybookNode[] = [
    { id: "start", type: "start", x: 60, y: 60, next: null, otherwise: null, wait_hours: 24, condition: "hot", text: "" },
    ...body,
    { id: "end", type: "end", x: 60, y: 60, next: null, otherwise: null, wait_hours: 24, condition: "hot", text: "" },
  ];
  const nodes = chain.map((node, index, all) => ({
    ...node,
    id: node.id || `step-${index}`,
    y: 60 + index * 80, // 50 nodes stay inside the 4000px canvas limit
    next: index < all.length - 1 ? (all[index + 1].id || `step-${index + 1}`) : null,
  }));
  return {
    name, kind, active: false, nodes,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
    preferred_hour: 9, optimize_time: true, version: 0,
  };
}

function requirePermission(
  permissions: ReadonlySet<string>,
  permission: string,
) {
  if (!permissions.has(permission)) {
    throw new Error(`Your workspace role does not allow this action (${permission}).`);
  }
}

// Every page Ask Corral can open, its visible name and the permissions that
// unlock it. Mirrors the app's view permissions and the server's area list.
export const ASSISTANT_AREAS: Record<WorkspaceAssistantArea, { label: string; permissions: string[] }> = {
  dashboard: { label: "Today", permissions: ["analytics.view"] },
  leads: { label: "Leads", permissions: ["leads.view"] },
  pipeline: { label: "Pipeline", permissions: ["analytics.view"] },
  revenue: { label: "Revenue", permissions: ["analytics.view"] },
  communication: { label: "Communication", permissions: ["communication.view"] },
  review: { label: "Review", permissions: ["manual_review.view"] },
  email: { label: "Inbox", permissions: ["integrations.view"] },
  calendar: { label: "Calendar", permissions: ["leads.view"] },
  dialer: { label: "Calls", permissions: ["dialer.call"] },
  contacts: { label: "Contacts", permissions: ["leads.view"] },
  accounts: { label: "Companies", permissions: ["leads.view"] },
  performance: { label: "Team Performance", permissions: ["analytics.view"] },
  analytics: { label: "Analytics", permissions: ["analytics.view"] },
  agents: { label: "AI Activity", permissions: ["agents.view"] },
  intelligence: { label: "Research & Coaching", permissions: ["agents.view"] },
  automations: { label: "Automations", permissions: ["agents.view"] },
  integrations: { label: "Integrations", permissions: ["integrations.view"] },
  team: { label: "Team", permissions: ["members.view"] },
  settings: { label: "Settings", permissions: ["routing.view", "integrations.view", "workspace.manage"] },
  icp: { label: "Ideal customer profile", permissions: ["icp.view"] },
  policies: { label: "Routing & capacity", permissions: ["routing.view"] },
  compliance: { label: "Consent & governance", permissions: ["workspace.manage"] },
};

export function canOpenAssistantArea(
  area: WorkspaceAssistantArea | null,
  permissions: ReadonlySet<string>,
): area is WorkspaceAssistantArea {
  if (!area) return false;
  return ASSISTANT_AREAS[area].permissions.some((permission) => permissions.has(permission));
}

export function canApplyWorkspaceAssistantAction(
  action: WorkspaceAssistantAction,
  permissions: ReadonlySet<string>,
): boolean {
  if (action.kind === "open_module") {
    return canOpenAssistantArea(action.area, permissions);
  }
  if (action.kind === "open_lead") {
    return Boolean(action.target_id && permissions.has("leads.view"));
  }
  if (action.kind === "open_deal_desk") {
    return Boolean(action.target_id && permissions.has("leads.view"));
  }
  if (["update_lead_profile", "add_lead_note", "record_lead_progress", "reevaluate_lead"].includes(action.kind)) {
    return Boolean(action.target_id && permissions.has("leads.view") && permissions.has("leads.edit"));
  }
  if (action.kind === "enroll_lead_in_automation") {
    return Boolean(action.target_id && permissions.has("leads.view") && permissions.has("leads.edit"));
  }
  if (action.kind === "complete_lead_manual_review") {
    return Boolean(
      action.target_id &&
      permissions.has("leads.view") &&
      permissions.has("manual_review.manage"),
    );
  }
  const requiredPermission: Partial<Record<WorkspaceAssistantAction["kind"], string>> = {
    set_automation_active: "workspace.manage",
    create_icp_draft: "icp.manage",
    create_automation_draft: "workspace.manage",
    update_routing_config: "routing.manage",
    set_lead_confirmation: "integrations.manage",
    disconnect_integration: "integrations.disconnect",
    invite_member: "members.invite",
    update_member_role: "members.manage",
    update_member_permissions: "members.manage",
    suspend_member: "members.manage",
    reactivate_member: "members.manage",
    send_email: "leads.edit",
    create_lead: "leads.edit",
  };
  const required = requiredPermission[action.kind];
  return action.kind === "none" || Boolean(required && permissions.has(required));
}

export async function applyWorkspaceAssistantAction(
  action: WorkspaceAssistantAction,
  permissions: ReadonlySet<string>,
  onNavigate: (area: WorkspaceAssistantArea) => void,
  onInspectLead: (leadId: string, section?: "deal") => void,
): Promise<string> {
  switch (action.kind) {
    case "create_lead":
      throw new Error("Create this lead from its reviewed draft card.");
    case "complete_lead_manual_review": {
      requirePermission(permissions, "leads.view");
      requirePermission(permissions, "manual_review.manage");
      if (!action.target_id) throw new Error("Choose a lead before completing its review.");
      await completeLeadManualReview(action.target_id);
      return `Completed the manual review for ${action.target_label || "the lead"} and applied current routing. The assignee may be notified.`;
    }
    case "update_lead_profile":
    case "add_lead_note":
    case "record_lead_progress":
    case "reevaluate_lead": {
      requirePermission(permissions, "leads.view");
      requirePermission(permissions, "leads.edit");
      if (!action.target_id) throw new Error("Choose a lead before applying changes.");
      const target = action.target_id;
      if (action.kind === "update_lead_profile") {
        const payload = parsePayload(action);
        await updateLeadProfile(target, payload as Parameters<typeof updateLeadProfile>[1]);
        return `Updated ${action.target_label || "the lead"}’s details.`;
      }
      if (action.kind === "add_lead_note") {
        const payload = parsePayload(action);
        if (typeof payload.note !== "string" || !payload.note.trim()) throw new Error("Provide a note to save.");
        await saveLeadNote(target, payload.note.trim());
        try {
          await reevaluateLead(target);
          return `Saved a note on ${action.target_label || "the lead"} and updated its AI assessment.`;
        } catch {
          return `Saved the note on ${action.target_label || "the lead"}, but its AI assessment could not be refreshed.`;
        }
      }
      if (action.kind === "record_lead_progress") {
        const payload = parsePayload(action);
        const result = await updateLeadProgress(
          target,
          payload as Parameters<typeof updateLeadProgress>[1],
        );
        return `Recorded ${String(payload.stage)} for ${action.target_label || "the lead"} and re-evaluated qualification as ${result.classification} (${result.score}/100).`;
      }
      await reevaluateLead(target);
      return `Re-evaluated ${action.target_label || "the lead"}’s qualification and routing.`;
    }
    case "open_module":
      if (!canOpenAssistantArea(action.area, permissions) || !action.area) {
        throw new Error("You do not have access to that workspace area.");
      }
      onNavigate(action.area);
      return action.area === "dialer"
        ? "Opened Calls. Corral has not placed a call."
        : `Opened ${ASSISTANT_AREAS[action.area].label}.`;
    case "open_lead":
      requirePermission(permissions, "leads.view");
      if (!action.target_id) {
        throw new Error("The lead selection is incomplete.");
      }
      onInspectLead(action.target_id);
      return `Opened ${action.target_label || "the selected lead"}.`;
    case "open_deal_desk":
      requirePermission(permissions, "leads.view");
      if (!action.target_id) {
        throw new Error("Choose a verified lead before opening its Deal Desk.");
      }
      onInspectLead(action.target_id, "deal");
      return `Opened the Deal Desk for ${action.target_label || "the verified lead"}. No quote was created, approved, or sent.`;
    case "set_automation_active": {
      requirePermission(permissions, "workspace.manage");
      if (!action.target_id || typeof action.enabled !== "boolean") {
        throw new Error("The automation proposal is incomplete.");
      }
      const { items } = await opsGet<{ items: Automation[] }>("/v1/automations");
      const target = items.find((item) => item.id === action.target_id);
      if (!target) throw new Error("That automation is no longer available.");
      await opsSave(
        `/v1/automations/${target.id}`,
        { ...target.specification, active: action.enabled },
        "PUT",
      );
      return `${target.name} is now ${action.enabled ? "active" : "paused"}.`;
    }
    case "create_icp_draft": {
      requirePermission(permissions, "icp.manage");
      const payload = parsePayload(action);
      const draft = await createIcpDraft({
        name: typeof payload.name === "string" ? payload.name : action.title,
        description: typeof payload.description === "string" ? payload.description : "",
        based_on_version_id:
          typeof payload.based_on_version_id === "string"
            ? payload.based_on_version_id
            : undefined,
      });
      if (payload.configuration && typeof payload.configuration === "object") {
        if (!draft.id) throw new Error("The ICP draft was created but could not be loaded for editing.");
        await updateIcpDraft(draft.id, {
          configuration: payload.configuration as IcpConfiguration,
        });
      }
      return `Created ICP draft “${draft.name}”. It has not been published.`;
    }
    case "create_automation_draft": {
      requirePermission(permissions, "workspace.manage");
      const spec = automationDraftSpec(parsePayload(action));
      await opsSave(`/v1/automations/${createClientId()}`, spec, "PUT");
      return `Saved “${spec.name}” as a paused ${spec.kind} draft with ${spec.nodes.length - 2} steps. Review and activate it in Automations; nobody is enrolled.`;
    }
    case "enroll_lead_in_automation": {
      requirePermission(permissions, "leads.view");
      requirePermission(permissions, "leads.edit");
      if (!action.target_id) throw new Error("Choose a verified lead first.");
      const payload = parsePayload(action);
      if (typeof payload.definition_id !== "string") throw new Error("Choose which automation to use.");
      const { items } = await opsGet<{ items: Automation[] }>("/v1/automations");
      const target = items.find((item) => item.id === payload.definition_id);
      if (!target) throw new Error("That automation is no longer available.");
      if (!target.active) throw new Error(`${target.name} is paused. Activate it in Automations before enrolling leads.`);
      await opsSave(`/v1/automations/${target.id}/enroll`, { lead_id: action.target_id });
      return `Enrolled ${action.target_label || "the lead"} in ${target.name}. Its steps create drafts for review; nothing sends automatically.`;
    }
    case "update_routing_config": {
      requirePermission(permissions, "routing.manage");
      const payload = parsePayload(action);
      await updateRoutingConfig(
        payload.member_percentages as Record<string, number> | undefined,
        payload.manual_review as Parameters<typeof updateRoutingConfig>[1],
      );
      return "Updated workspace routing settings.";
    }
    case "set_lead_confirmation":
      requirePermission(permissions, "integrations.manage");
      if (typeof action.enabled !== "boolean") {
        throw new Error("The email-confirmation setting is missing its new value.");
      }
      await setLeadConfirmationAutomation(action.enabled);
      return `Automatic lead confirmations are now ${action.enabled ? "on" : "off"}.`;
    case "disconnect_integration":
      requirePermission(permissions, "integrations.disconnect");
      if (!action.target_id) throw new Error("Choose a connected integration first.");
      await disconnectIntegration(action.target_id);
      return "Disconnected the selected integration.";
    case "invite_member":
      requirePermission(permissions, "members.invite");
      if (!action.email || !action.role) {
        throw new Error("The invitation needs both an email address and a role.");
      }
      await inviteMember({ email: action.email, role: action.role });
      return `Sent a workspace invitation to ${action.email}.`;
    case "update_member_role":
      requirePermission(permissions, "members.manage");
      if (!action.target_id || !action.role) {
        throw new Error("The member role proposal is incomplete.");
      }
      await updateMemberRole(action.target_id, action.role);
      return "Updated the member’s workspace role.";
    case "update_member_permissions": {
      requirePermission(permissions, "members.manage");
      if (!action.target_id) throw new Error("Choose a workspace member first.");
      const payload = parsePayload(action);
      await updateMemberPermissions(action.target_id, {
        grants: Array.isArray(payload.grants) ? payload.grants.filter((x): x is string => typeof x === "string") : [],
        denies: Array.isArray(payload.denies) ? payload.denies.filter((x): x is string => typeof x === "string") : [],
      });
      return "Updated the member’s workspace permissions.";
    }
    case "suspend_member":
      requirePermission(permissions, "members.manage");
      if (!action.target_id) throw new Error("Choose a workspace member first.");
      await suspendMember(action.target_id);
      return "Suspended the selected workspace member.";
    case "reactivate_member":
      requirePermission(permissions, "members.manage");
      if (!action.target_id) throw new Error("Choose a workspace member first.");
      await reactivateMember(action.target_id);
      return "Reactivated the selected workspace member.";
    case "send_email":
      throw new Error("Review and send email drafts from the email approval card.");
    case "none":
      return "No change was made.";
    default:
      throw new Error("This proposal is not supported.");
  }
}
