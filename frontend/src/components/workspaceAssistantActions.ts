import {
  createIcpDraft,
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
  type WorkspaceAssistantAction,
  type WorkspaceAssistantArea,
} from "../api";
import type { IcpConfiguration } from "../types";
import { opsGet, opsSave, type Automation } from "../operationsApi";

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

function requirePermission(
  permissions: ReadonlySet<string>,
  permission: string,
) {
  if (!permissions.has(permission)) {
    throw new Error(`Your workspace role does not allow this action (${permission}).`);
  }
}

export function canOpenAssistantArea(
  area: WorkspaceAssistantArea | null,
  permissions: ReadonlySet<string>,
): area is WorkspaceAssistantArea {
  if (!area) return false;
  const permissionByArea: Record<WorkspaceAssistantArea, string[]> = {
    analytics: ["analytics.view"],
    automations: ["agents.view"],
    icp: ["icp.view"],
    settings: ["routing.view", "integrations.view", "workspace.manage"],
    integrations: ["integrations.view"],
    team: ["members.view"],
    dialer: ["dialer.call"],
  };
  return permissionByArea[area].some((permission) => permissions.has(permission));
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
  if (["update_lead_profile", "add_lead_note", "record_lead_progress", "reevaluate_lead"].includes(action.kind)) {
    return Boolean(action.target_id && permissions.has("leads.view") && permissions.has("leads.edit"));
  }
  const requiredPermission: Partial<Record<WorkspaceAssistantAction["kind"], string>> = {
    set_automation_active: "workspace.manage",
    create_icp_draft: "icp.manage",
    update_routing_config: "routing.manage",
    set_lead_confirmation: "integrations.manage",
    disconnect_integration: "integrations.disconnect",
    invite_member: "members.invite",
    update_member_role: "members.manage",
    update_member_permissions: "members.manage",
    suspend_member: "members.manage",
    reactivate_member: "members.manage",
    send_email: "leads.edit",
  };
  const required = requiredPermission[action.kind];
  return action.kind === "none" || Boolean(required && permissions.has(required));
}

export async function applyWorkspaceAssistantAction(
  action: WorkspaceAssistantAction,
  permissions: ReadonlySet<string>,
  onNavigate: (area: WorkspaceAssistantArea) => void,
  onInspectLead: (leadId: string) => void,
): Promise<string> {
  switch (action.kind) {
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
        await saveLeadNote(target, payload.note);
        return `Saved a note on ${action.target_label || "the lead"}.`;
      }
      if (action.kind === "record_lead_progress") {
        const payload = parsePayload(action);
        await updateLeadProgress(target, payload as Parameters<typeof updateLeadProgress>[1]);
        return `Recorded ${String(payload.stage)} for ${action.target_label || "the lead"}.`;
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
        ? "Opened the Dialer. Corral has not placed a call."
        : `Opened ${action.area}.`;
    case "open_lead":
      requirePermission(permissions, "leads.view");
      if (!action.target_id) {
        throw new Error("The lead selection is incomplete.");
      }
      onInspectLead(action.target_id);
      return `Opened ${action.target_label || "the selected lead"}.`;
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
