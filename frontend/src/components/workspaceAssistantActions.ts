import {
  createIcpDraft,
  disconnectIntegration,
  getDashboard,
  getLeadConfirmationAutomation,
  getRoutingConfig,
  inviteMember,
  listIntegrations,
  listInvitations,
  listIcpVersions,
  listMembers,
  reactivateMember,
  setLeadConfirmationAutomation,
  suspendMember,
  updateIcpDraft,
  updateMemberPermissions,
  updateMemberRole,
  updateRoutingConfig,
  type WorkspaceAssistantAction,
  type WorkspaceAssistantArea,
} from "../api";
import type { IcpConfiguration } from "../types";
import { opsGet, opsSave, type Automation } from "../operationsApi";

export async function loadWorkspaceAssistantContext(
  permissions: ReadonlySet<string>,
): Promise<Record<string, unknown>> {
  const entries: Array<[string, Promise<unknown>]> = [];
  if (permissions.has("analytics.view")) {
    entries.push(["analytics_monthly", getDashboard("monthly")]);
  }
  if (permissions.has("agents.view")) {
    entries.push([
      "automations",
      opsGet<{ items: Automation[] }>("/v1/automations").then(({ items }) =>
        items.slice(0, 20).map(({ id, name, kind, active, version, specification }) => ({
          id,
          name,
          kind,
          active,
          version,
          specification,
        })),
      ),
    ]);
  }
  if (permissions.has("icp.view")) {
    entries.push([
      "icp_versions",
      listIcpVersions().then((items) =>
        items.slice(0, 10).map(({ id, name, description, status, configuration }) => ({
          id,
          name,
          description,
          status,
          configuration,
        })),
      ),
    ]);
  }
  if (permissions.has("routing.view")) {
    entries.push(["routing_settings", getRoutingConfig()]);
  }
  if (permissions.has("integrations.view")) {
    entries.push(["lead_confirmation_setting", getLeadConfirmationAutomation()]);
    entries.push([
      "integrations",
      listIntegrations().then((cards) =>
        cards.map((card) => ({
          provider: card.provider,
          connections: card.connections.map((connection) => ({
            id: connection.id,
            account_name: connection.account_name,
            status: connection.status,
            is_active: connection.is_active,
          })),
        })),
      ),
    ]);
  }
  if (permissions.has("members.view")) {
    entries.push([
      "team",
      Promise.all([listMembers(), listInvitations()]).then(([members, invitations]) => ({
        members: members.slice(0, 40),
        invitations: invitations.slice(0, 40),
      })),
    ]);
  }

  const context: Record<string, unknown> = {
    available_areas: [
      permissions.has("analytics.view") && "analytics",
      permissions.has("agents.view") && "automations",
      permissions.has("icp.view") && "icp",
      (permissions.has("routing.view") || permissions.has("integrations.view")) &&
        "settings",
      permissions.has("integrations.view") && "integrations",
      permissions.has("members.view") && "team",
    ].filter(Boolean),
  };
  await Promise.all(
    entries.map(async ([key, request]) => {
      try {
        context[key] = await request;
      } catch {
        // A denied or unavailable view is omitted; write APIs still enforce their own permissions.
      }
    }),
  );
  return context;
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
  };
  const required = requiredPermission[action.kind];
  return action.kind === "none" || Boolean(required && permissions.has(required));
}

export async function applyWorkspaceAssistantAction(
  action: WorkspaceAssistantAction,
  permissions: ReadonlySet<string>,
  onNavigate: (area: WorkspaceAssistantArea) => void,
): Promise<string> {
  switch (action.kind) {
    case "open_module":
      if (!canOpenAssistantArea(action.area, permissions) || !action.area) {
        throw new Error("You do not have access to that workspace area.");
      }
      onNavigate(action.area);
      return `Opened ${action.area}.`;
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
    case "none":
      return "No change was made.";
    default:
      throw new Error("This proposal is not supported.");
  }
}
