import { OperationsDialog } from "../components/OperationsDialog";
import { TeamScopePanel } from "../components/TeamScopePanel";
import { Icon } from "../v3/ui/Icon";
import { useCallback,useEffect,useMemo,useState } from "react";

import {
ApiError,
deleteConnectionGrant,
inviteMember,
listConnectionGrants,
listIntegrations,
listInvitations,
listMemberAuditEvents,
listMembers,
reactivateMember,
removeMember,
resendInvitation,
revokeInvitation,
suspendMember,
updateMemberPermissions,
updateMemberAccessPolicy,
updateMemberRole,
updateMemberSlackLink,
updateConnectionGrant,
} from "../api";
import { can,defaultPermissionsForRole,PERMISSION_GROUPS,ROLE_LABELS,ROLE_OPTIONS,roleLabel,statusLabel } from "../permissions";
import type {
AuthSession,
AccessPolicy,
IntegrationCard,
PermissionOverrides,
ProviderConnection,
WorkspaceAuditEvent,
WorkspaceInvitation,
WorkspaceMember,
} from "../types";
import { PageHeader } from "../v3/ui/primitives";

type TeamViewProps = {
  auth: AuthSession;
  workspaceName: string;
  onBack: () => void;
};

type PermissionEditorProps = {
  overrides: PermissionOverrides;
  effective?: ReadonlySet<string>;
  readOnly?: boolean;
  onChange?: (overrides: PermissionOverrides) => void;
};

type Confirmation = {
  title: string;
  detail: string;
  confirmLabel: string;
  danger?: boolean;
  action: () => Promise<void>;
};

const emptyOverrides = (): PermissionOverrides => ({ grants: [], denies: [] });
const emptyAccessPolicy = (): AccessPolicy => ({ scopes: {} });
const scopedPermissions = [
  { id: "leads.view", label: "Lead visibility" },
  { id: "leads.edit", label: "Lead editing" },
  { id: "analytics.view", label: "Analytics visibility" },
] as const;

function AccessScopeEditor({ policy, readOnly, onChange }: { policy: AccessPolicy; readOnly?: boolean; onChange?: (policy: AccessPolicy) => void }) {
  return <div className="permission-groups">
    <section className="permission-group">
      <h3>Record scopes</h3>
      <p className="permission-edit-hint">Scope controls which records an allowed action can reach. Explicit lead shares remain separate and never grant mailbox access.</p>
      <div>
        {scopedPermissions.map((permission) => <label className="team-field" key={permission.id}>
          <span>{permission.label}</span>
          <select
            value={policy.scopes[permission.id] ?? ""}
            disabled={readOnly}
            onChange={(event) => {
              const scopes = { ...policy.scopes };
              if (event.target.value) scopes[permission.id] = event.target.value as AccessPolicy["scopes"][string];
              else delete scopes[permission.id];
              onChange?.({ scopes });
            }}
          >
            <option value="">Use role default</option>
            <option value="none">None</option>
            <option value="own">Own / assigned</option>
            <option value="team">Managed teams</option>
            <option value="all">All workspace records</option>
            <option value="custom">Explicitly shared only</option>
          </select>
        </label>)}
      </div>
    </section>
  </div>;
}

function ConnectionAccessEditor({
  memberId,
  connections,
  editable,
}: {
  memberId: string;
  connections: ProviderConnection[];
  editable: boolean;
}) {
  const [granted, setGranted] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setError(null);
    void Promise.all(
      connections.map(async (connection) => ({
        id: connection.id,
        grants: await listConnectionGrants(connection.id),
      })),
    ).then((rows) => {
      if (!active) return;
      setGranted(new Set(
        rows
          .filter((row) => row.grants.some((grant) => grant.membership_id === memberId))
          .map((row) => row.id),
      ));
    }).catch((requestError) => {
      if (active) setError(friendlyError(requestError));
    });
    return () => { active = false; };
  }, [connections, memberId]);

  if (!connections.length) {
    return <p className="permission-edit-hint">No delegatable team-shared or workspace-managed connections are available.</p>;
  }
  return <div className="permission-groups">
    <section className="permission-group">
      <p className="permission-edit-hint">Delegation permits this member to use a shared account only when their provider permission also allows the requested action. Personal mailboxes can never be delegated.</p>
      {error && <div className="team-alert" role="alert"><span>{error}</span></div>}
      <div>
        {connections.map((connection) => {
          const isGranted = granted.has(connection.id);
          return <button
            type="button"
            key={connection.id}
            className={`permission-toggle ${isGranted ? "grant" : "none"}`}
            disabled={!editable || busy === connection.id}
            aria-pressed={isGranted}
            onClick={async () => {
              setBusy(connection.id);
              setError(null);
              try {
                if (isGranted) {
                  await deleteConnectionGrant(connection.id, memberId);
                  setGranted((current) => {
                    const next = new Set(current);
                    next.delete(connection.id);
                    return next;
                  });
                } else {
                  await updateConnectionGrant(
                    connection.id,
                    memberId,
                    ["view", "use", "sync", "send"],
                  );
                  setGranted((current) => new Set([...current, connection.id]));
                }
              } catch (requestError) {
                setError(friendlyError(requestError));
              } finally {
                setBusy(null);
              }
            }}
          >
            <i aria-hidden="true">{isGranted ? "✓" : "·"}</i>
            <span>
              <b>{connection.account_name || connection.provider_type}</b>
              <small>{connection.provider_type.replaceAll("_", " ")} · {connection.connection_type?.replaceAll("_", " ") ?? "shared"}</small>
            </span>
          </button>;
        })}
      </div>
    </section>
  </div>;
}

function friendlyError(error: unknown) {
  const message = error instanceof Error ? error.message : "The request could not be completed.";
  const friendlyMessage = error instanceof ApiError && error.status >= 500
    ? "Corral could not confirm this action. Refresh the page to check whether it completed before trying again."
    : message.includes("permission required")
    ? "You do not have permission to do that in this workspace."
    : message.includes("already a workspace member")
      ? "That person already belongs to this workspace."
      : message.includes("already been used")
        ? "This invitation has already been accepted or revoked."
        : message.includes("expired")
          ? "This invitation has expired. Resend it to create a fresh invitation."
          : message.includes("ownership") || message.includes("protected")
            ? "Workspace ownership is protected and cannot be changed here."
            : message;
  const requestId = error instanceof ApiError ? error.requestId : undefined;
  return requestId ? `${friendlyMessage} Reference ID: ${requestId}` : friendlyMessage;
}

function formatDate(value: string | null | undefined) {
  if (!value) return "—";
  return new Date(value).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function overrideState(
  permission: string,
  overrides: PermissionOverrides,
  effective?: ReadonlySet<string>,
): "grant" | "deny" | "inherited" | "none" {
  if (overrides.denies.includes(permission)) return "deny";
  if (overrides.grants.includes(permission)) return "grant";
  return effective?.has(permission) ? "inherited" : "none";
}

function cycleOverride(
  permission: string,
  overrides: PermissionOverrides,
  effective?: ReadonlySet<string>,
): PermissionOverrides {
  const current = overrideState(permission, overrides, effective);
  const next = current === "inherited" ? "deny" : current === "none" ? "grant" : current === "grant" ? "deny" : "none";
  return {
    grants: next === "grant"
      ? [...overrides.grants.filter((item) => item !== permission), permission].sort()
      : overrides.grants.filter((item) => item !== permission),
    denies: next === "deny"
      ? [...overrides.denies.filter((item) => item !== permission), permission].sort()
      : overrides.denies.filter((item) => item !== permission),
  };
}

const INTEGRATION_CATEGORY_PROVIDERS: Array<[string, string[]]> = [
  ["CRM integrations", ["Salesforce", "Hubspot", "Gohighlevel"]],
  ["Email & calendar", ["Gmail", "Outlook", "Google Calendar", "Calendly"]],
  ["Dialer & calling", ["Cloudtalk", "Twilio", "Aircall", "Ringcentral", "Vapi", "Retell"]],
  ["AI providers", ["Openai", "Gemini", "Claude", "Deepseek", "Xai"]],
  ["Social & sources", ["Slack", "Linkedin", "Instagram", "Messenger", "Whatsapp Business", "Clutch"]],
];

const CORE_PERMISSION_CATEGORIES: Array<[string, string[]]> = [
  ["Leads", ["Leads"]],
  ["Collaboration", ["Collaboration"]],
  ["Analytics", ["Analytics"]],
  ["Review & routing", ["Manual review", "Routing"]],
  ["Integration settings", ["Integrations"]],
  ["ICP", ["ICP"]],
  ["Team & workspace", ["Team", "Workspace"]],
  ["AI & automation", ["AI / Agents"]],
  ["Dialer & calling", ["Dialer", "Calls & recordings"]],
  ["Mailbox", ["Mailbox"]],
  ["Sensitive fields", ["Sensitive fields"]],
];

const permissionGroupsByLabel = new Map(PERMISSION_GROUPS.map((group) => [group.label, group]));
const corePermissionGroups = CORE_PERMISSION_CATEGORIES.map(([label, sourceLabels]) => ({
  label,
  permissions: sourceLabels.flatMap((sourceLabel) => {
    const group = permissionGroupsByLabel.get(sourceLabel);
    return group?.permissions.map((permission) => sourceLabels.length > 1
      ? { ...permission, label: `${sourceLabel} · ${permission.label}` }
      : permission) ?? [];
  }),
}));

const DISPLAY_PERMISSION_GROUPS = [
  ...corePermissionGroups,
  ...INTEGRATION_CATEGORY_PROVIDERS.map(([label, providers]) => ({
    label,
    permissions: PERMISSION_GROUPS
      .filter((group) => providers.includes(group.label.replace("Integration · ", "")))
      .flatMap((group) => group.permissions.map((permission) => ({
        ...permission,
        label: `${group.label.replace("Integration · ", "")} · ${permission.label}`,
      }))),
  })),
];

function PermissionEditor({ overrides, effective, readOnly, onChange }: PermissionEditorProps) {
  const [query, setQuery] = useState("");
  // Keep the editor scannable: open the highest-signal group only, while
  // search still reveals matching permissions across every group.
  const [openGroups, setOpenGroups] = useState<Set<string>>(() => new Set(["Leads"]));
  const normalizedQuery = query.trim().toLowerCase();
  const visibleGroups = DISPLAY_PERMISSION_GROUPS.map((group) => ({
    ...group,
    permissions: group.permissions.filter((permission) => !normalizedQuery || `${group.label} ${permission.label}`.toLowerCase().includes(normalizedQuery)),
  })).filter((group) => group.permissions.length > 0);
  const toggleGroup = (label: string) => setOpenGroups((current) => {
    const next = new Set(current);
    if (next.has(label)) next.delete(label); else next.add(label);
    return next;
  });
  return (
    <div className="permission-editor">
      <div className="permission-toolbar">
        <label className="permission-search">
          <span className="sr-only">Search permissions</span>
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search permissions or groups" />
        </label>
        <span className="permission-count">{visibleGroups.reduce((count, group) => count + group.permissions.length, 0)} shown</span>
      </div>
      <p className="permission-edit-hint">Click an inherited permission to deny it, or a missing one to grant it. Integration access is grouped by work area, with each provider still controlled separately.</p>
      <div className="permission-groups">
        {visibleGroups.map((group) => {
          const open = Boolean(normalizedQuery) || openGroups.has(group.label);
          const activeCount = group.permissions.filter((permission) => {
            const state = overrideState(permission.id, overrides, effective);
            return state === "grant" || state === "inherited";
          }).length;
          return (
          <section className="permission-group" key={group.label}>
            <button type="button" className="permission-group-heading" aria-expanded={open} onClick={() => toggleGroup(group.label)}>
              <span><b>{group.label}</b><small>{activeCount} of {group.permissions.length} active</small></span>
              <i aria-hidden="true">{open ? "−" : "+"}</i>
            </button>
            {open && <div>
              {group.permissions.map((permission) => {
                const state = overrideState(permission.id, overrides, effective);
                return (
                  <button
                    type="button"
                    key={permission.id}
                    className={`permission-toggle ${state}`}
                    disabled={readOnly}
                    aria-label={`${permission.label}: ${state}`}
                    aria-pressed={state === "grant" || state === "inherited"}
                    onClick={() => onChange?.(cycleOverride(permission.id, overrides, effective))}
                  >
                    <i aria-hidden="true">{state === "deny" ? "–" : state === "none" ? "·" : "✓"}</i>
                    <span><b>{permission.label}</b><small>{state === "inherited" ? "From role" : state === "grant" ? "Custom grant" : state === "deny" ? "Explicit deny" : "Not granted"}</small></span>
                  </button>
                );
              })}
            </div>}
          </section>
          );
        })}
        {!visibleGroups.length && <p className="permission-empty">No permissions match “{query}”.</p>}
      </div>
    </div>
  );
}

function MemberStatus({ status }: { status: string }) {
  return <span className={`team-status status-${status}`}><i />{statusLabel(status)}</span>;
}

function RolePill({ role }: { role: string }) {
  return <span className={`team-role role-${role}`}>{roleLabel(role)}</span>;
}

function AuditLine({ event, members }: { event: WorkspaceAuditEvent; members: WorkspaceMember[] }) {
  const target = members.find((member) => member.id === event.target_membership_id);
  const afterRole = typeof event.after.role === "string" ? event.after.role : null;
  const beforeRole = typeof event.before.role === "string" ? event.before.role : null;
  let text = "Workspace administration updated";
  if (event.operation === "member_invited") text = `Invited ${String(event.after.email ?? "a new member")}`;
  if (event.operation === "invitation_revoked") text = `Revoked invitation for ${String(event.after.email ?? "a pending member")}`;
  if (event.operation === "member_joined") text = `${target?.display_name ?? "A member"} joined the workspace`;
  if (event.operation === "role_changed") text = `Changed ${target?.display_name ?? "a member"} from ${roleLabel(beforeRole ?? "member")} to ${roleLabel(afterRole ?? "member")}`;
  if (event.operation === "permissions_changed") text = `Updated permissions for ${target?.display_name ?? "a member"}`;
  if (event.operation === "member_suspended") text = `Suspended ${target?.display_name ?? "a member"}`;
  if (event.operation === "member_reactivated") text = `Reactivated ${target?.display_name ?? "a member"}`;
  if (event.operation === "member_removed") text = `Removed ${target?.display_name ?? "a member"}`;
  if (event.operation === "slack_member_link_changed") text = `Updated the Slack link for ${target?.display_name ?? "a member"}`;
  return <li><span className="audit-marker" /><div><b>{text}</b><small>{formatDate(event.created_at)}</small></div></li>;
}

export function TeamView({ auth, workspaceName, onBack }: TeamViewProps) {
  const [matrixRole, setMatrixRole] = useState("sales_rep");
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [invitations, setInvitations] = useState<WorkspaceInvitation[]>([]);
  const [audit, setAudit] = useState<WorkspaceAuditEvent[]>([]);
  const [integrations, setIntegrations] = useState<IntegrationCard[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detailsCollapsed, setDetailsCollapsed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState("sales_rep");
  const [inviteOverrides, setInviteOverrides] = useState<PermissionOverrides>(emptyOverrides);
  const [inviteAccessPolicy, setInviteAccessPolicy] = useState<AccessPolicy>(emptyAccessPolicy);
  const [inviteBusy, setInviteBusy] = useState(false);
  const [generatedInvite, setGeneratedInvite] = useState<WorkspaceInvitation | null>(null);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const sessionPermissions = auth.workspaces.find((workspace) => workspace.id === auth.current_workspace_id)?.effective_permissions ?? [];
  const canReadAdminHistory = sessionPermissions.includes("members.manage");
  const canReadInvitations = canReadAdminHistory || sessionPermissions.includes("members.invite");
  const canManageConnections = canReadAdminHistory && sessionPermissions.includes("integrations.manage");

  const load = useCallback(async (
    signal?: AbortSignal,
    preserve = false,
    suppressErrors = false,
  ): Promise<boolean> => {
    if (preserve) setRefreshing(true); else setLoading(true);
    if (!suppressErrors) setError(null);
    try {
      const [nextMembers, nextInvitations, nextAudit, nextIntegrations] = await Promise.all([
        listMembers(signal),
        canReadInvitations ? listInvitations(signal) : Promise.resolve([]),
        canReadAdminHistory ? listMemberAuditEvents(signal) : Promise.resolve([]),
        canManageConnections ? listIntegrations() : Promise.resolve([] as IntegrationCard[]),
      ]);
      if (signal?.aborted) return false;
      setMembers(nextMembers);
      setInvitations(nextInvitations);
      setAudit(nextAudit);
      setIntegrations(nextIntegrations);
      setSelectedId((current) => current && nextMembers.some((member) => member.id === current) ? current : null);
      return true;
    } catch (requestError) {
      if (!signal?.aborted && !suppressErrors) setError(friendlyError(requestError));
      return false;
    } finally {
      if (!signal?.aborted) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [canManageConnections, canReadAdminHistory, canReadInvitations]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load, auth.current_workspace_id]);

  const actor = useMemo(() => members.find((member) => member.user_id === auth.user.id), [auth.user.id, members]);
  const actorPermissions = useMemo(() => new Set(actor?.effective_permissions ?? []), [actor]);
  const selected = members.find((member) => member.id === selectedId) ?? null;
  const canView = can(actorPermissions, "members.view");
  const canInvite = can(actorPermissions, "members.invite");
  const canManage = can(actorPermissions, "members.manage");
  const delegatableConnections = useMemo(
    () => integrations.flatMap((card) => card.connections).filter(
      (connection, index, all) =>
        connection.connection_type !== "personal"
        && all.findIndex((item) => item.id === connection.id) === index,
    ),
    [integrations],
  );

  function showToast(message: string) {
    setToast(message);
    window.setTimeout(() => setToast(null), 3400);
  }

  async function runAction(action: () => Promise<void>, success: string) {
    setActionBusy(true);
    setError(null);
    try {
      await action();
      await load(undefined, true);
      showToast(success);
      setConfirmation(null);
    } catch (requestError) {
      setError(friendlyError(requestError));
    } finally {
      setActionBusy(false);
    }
  }

  async function submitInvitation(event: React.FormEvent) {
    event.preventDefault();
    setInviteBusy(true);
    setError(null);
    try {
      const invitation = await inviteMember({
        email: inviteEmail,
        role: inviteRole,
        permission_overrides: inviteOverrides,
        access_policy: inviteAccessPolicy,
      });
      setInviteOpen(false);
      setInviteEmail("");
      setInviteOverrides(emptyOverrides());
      setInviteAccessPolicy(emptyAccessPolicy());
      setInvitations((current) => [
        invitation,
        ...current.filter((item) => item.id !== invitation.id),
      ]);
      setGeneratedInvite(invitation);
      showToast("Access code created. Share it privately with the invited staff member.");
      void load(undefined, true, true).then((refreshed) => {
        if (!refreshed) {
          setError("Invitation created, but the team list could not refresh. The access code is available to copy.");
        }
      });
    } catch (requestError) {
      setError(friendlyError(requestError));
    } finally {
      setInviteBusy(false);
    }
  }

  const requestConfirmation = (next: Confirmation) => setConfirmation(next);

  return (
    <div className="page-view operational-page team-page">
      <div data-tour="team">
        <PageHeader
          back={{ label: "Settings", onClick: onBack }}
          title="Team"
          subtitle={<>People with access to <b>{workspaceName}</b>, their roles and permissions. Changes apply to this workspace only.</>}
        />
      </div>

      {error && <div className="team-alert" role="alert"><span>{error}</span><button onClick={() => setError(null)} aria-label="Dismiss message">×</button></div>}
      {!canView && !loading && <div className="team-denied panel"><span className="eyebrow">Members view</span><h2>Team visibility is restricted</h2><p>Your current workspace permissions do not include <code>members.view</code>. Ask an owner or admin to grant access.</p></div>}
      {canView && (
        <>
          <section className="team-summary-grid">
            <div className="team-summary panel"><span className="eyebrow">Members</span><strong>{members.length}</strong><small>{members.filter((member) => member.status === "active").length} active members</small></div>
            <div className="team-summary panel"><span className="eyebrow">Invitations</span><strong>{invitations.filter((invitation) => invitation.status === "pending").length}</strong><small>Waiting to be accepted</small></div>
            <div className="team-summary panel"><span className="eyebrow">Your access</span><strong>{actor ? roleLabel(actor.role) : "Resolving…"}</strong><small>{actorPermissions.size} permissions</small></div>
          </section>
          <section className="panel role-access-panel" style={{ padding: 20 }} aria-label="Role access defaults">
            <h2>Role access defaults</h2>
            <p>Owners and Admins have workspace access. Managers see explicitly managed teams; Sales Reps see assigned or shared leads only. Personal coaching remains limited to its recipient and authorized managers.</p>
            <label className="team-field"><span>Inspect a role</span><select value={matrixRole} onChange={(event) => setMatrixRole(event.target.value)}>{Object.entries(ROLE_LABELS).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
            <PermissionEditor overrides={emptyOverrides()} effective={defaultPermissionsForRole(matrixRole)} readOnly />
            <p className="permission-edit-hint">These are role defaults. Select a member to inspect their effective permissions and explicit exceptions.</p>
          </section>
          <section className="team-layout team-roster-layout">
            <TeamScopePanel members={members} canManage={canManage} />
            <div className="team-main-column">
              <div className="panel team-members-panel">
                <div className="section-heading"><div><span className="eyebrow">People with access</span><h2>Workspace users</h2></div><div className="team-heading-actions">{refreshing && <span className="inline-loading">Refreshing</span>}{canInvite && <button className="primary-button" onClick={() => setInviteOpen(true)}>+ Invite user</button>}</div></div>
                {loading ? <TeamListSkeleton /> : members.length ? <div className="team-member-list">{members.map((member) => <div className="team-member-row" key={member.id}>
                  <div className="member-person">
                    <span className="member-avatar">{member.display_name.slice(0, 1).toUpperCase()}</span>
                    <span className="member-identity">
                      <b>{member.display_name}{member.user_id === auth.user.id && <em>You</em>}</b>
                      <small>{member.email}</small>
                      <small className="member-permission-count">{member.effective_permissions.length} permissions · {member.slack_member_id ? `Slack ${member.slack_member_id}` : "Slack not linked"}</small>
                    </span>
                  </div>
                  <div className="member-row-side">
                    <div className="member-row-meta">
                      <RolePill role={member.role} />
                      <MemberStatus status={member.status} />
                    </div>
                    <span className="member-row-actions">
                      <button type="button" className="button-quiet" onClick={() => { setSelectedId(member.id); setDetailsCollapsed(false); }}>{canManage ? "Edit" : "View access"}</button>
                      {canManage && member.role !== "owner" && member.user_id !== auth.user.id && <button type="button" className="button-quiet danger-text" onClick={() => requestConfirmation({ title: "Remove from workspace?", detail: `${member.display_name} will lose access to this workspace. Their account and other workspace memberships will remain.`, confirmLabel: "Remove from workspace", danger: true, action: async () => { await removeMember(member.id); } })}>Delete</button>}
                    </span>
                  </div>
                </div>)}</div> : <div className="empty-state"><i /><b>No users yet</b><span>Invite the first person to this workspace.</span></div>}
              </div>
              <div className="panel team-invitations-panel">
                <div className="section-heading"><div><span className="eyebrow">Pending access</span><h2>Invitations</h2></div><span className="panel-action">One-time staff codes</span></div>
                {loading ? <div className="team-invite-skeleton" /> : invitations.filter((invitation) => invitation.status === "pending").length ? <div className="invitation-list">{invitations.filter((invitation) => invitation.status === "pending").map((invitation) => <div className="invitation-row" key={invitation.id}><span className="invitation-icon">✉</span><span><b>{invitation.email}</b><small>{roleLabel(invitation.role)} · Expires {formatDate(invitation.expires_at)}</small></span><span className="invitation-status">Code ready</span>{canManage && <span className="invitation-actions"><button onClick={() => requestConfirmation({ title: "Resend access code?", detail: `The old code for ${invitation.email} will stop working and a fresh code will be created.`, confirmLabel: "Resend code", action: async () => { const next = await resendInvitation(invitation.id); setGeneratedInvite(next); } })}>Resend code</button><button className="danger-text" onClick={() => requestConfirmation({ title: "Revoke invitation?", detail: `${invitation.email} will no longer be able to join from this invitation.`, confirmLabel: "Revoke invitation", danger: true, action: async () => { await revokeInvitation(invitation.id); } })}>Revoke</button></span>}</div>)}</div> : <div className="empty-state compact-empty"><b>No pending invitations</b><span>Invite staff to generate a private workspace ID and one-time access code.</span></div>}
              </div>
              <div className="panel team-audit-panel"><div className="section-heading"><div><span className="eyebrow">Durable administration trail</span><h2>Recent activity</h2></div><span className="panel-action">Last {Math.min(audit.length, 6)} events</span></div>{audit.length ? <ul className="team-audit-list">{audit.slice(0, 6).map((event) => <AuditLine key={event.id} event={event} members={members} />)}</ul> : <div className="compact-empty"><span>No administration activity recorded yet.</span></div>}</div>
            </div>
          </section>
          {selected && <div className="team-member-drawer-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setSelectedId(null); }}>
            <section className="team-member-drawer" role="dialog" aria-modal="true" aria-label={`Edit ${selected.display_name}`}>
              <button className="team-back" type="button" onClick={() => setSelectedId(null)}><Icon name="chevronLeft" size={14} />Back to members</button>
              <MemberDetail
              member={selected}
              actor={actor}
              canManage={canManage}
              connections={delegatableConnections}
              collapsed={detailsCollapsed}
              onToggleCollapsed={() => setDetailsCollapsed((current) => !current)}
              onSaveRole={async (role) => {
                if (!selected) return;
                await runAction(async () => {
                  const next = await updateMemberRole(selected.id, role);
                  setMembers((current) => current.map((item) => item.id === next.id ? next : item));
                }, "Role updated.");
              }}
              onSavePermissions={async (overrides) => {
                if (!selected) return;
                await runAction(async () => {
                  const next = await updateMemberPermissions(selected.id, overrides);
                  setMembers((current) => current.map((item) => item.id === next.id ? next : item));
                }, "Permissions updated.");
              }}
              onSaveAccessPolicy={async (policy) => {
                if (!selected) return;
                await runAction(async () => {
                  const next = await updateMemberAccessPolicy(selected.id, policy);
                  setMembers((current) => current.map((item) => item.id === next.id ? next : item));
                }, "Record scopes updated.");
              }}
              onSaveSlack={async (slackId) => {
                if (!selected) return;
                await runAction(async () => {
                  const next = await updateMemberSlackLink(selected.id, slackId.trim().toUpperCase() || null);
                  setMembers((current) => current.map((item) => item.id === next.id ? next : item));
                }, slackId ? "Slack member linked." : "Slack member link removed.");
              }}
              onSuspend={() => selected && requestConfirmation({
                title: "Suspend member?",
                detail: `${selected.display_name} will lose workspace access until reactivated.`,
                confirmLabel: "Suspend member",
                danger: true,
                action: async () => { await suspendMember(selected.id); },
              })}
              onReactivate={() => selected && runAction(() => reactivateMember(selected.id).then(() => undefined), "Member reactivated.")}
              onRemove={() => selected && requestConfirmation({
                title: "Remove from workspace?",
                detail: `${selected.display_name} will lose access to this workspace. Their account and other workspace memberships will remain.`,
                confirmLabel: "Remove from workspace",
                danger: true,
                action: async () => { await removeMember(selected.id); },
              })}
              />
            </section>
          </div>}
        </>
      )}
      {inviteOpen && <InviteDialog email={inviteEmail} role={inviteRole} overrides={inviteOverrides} accessPolicy={inviteAccessPolicy} busy={inviteBusy} onEmail={setInviteEmail} onRole={setInviteRole} onOverrides={setInviteOverrides} onAccessPolicy={setInviteAccessPolicy} onClose={() => setInviteOpen(false)} onSubmit={submitInvitation} />}
      {generatedInvite?.access_code && <InviteCodeDialog invitation={generatedInvite} onClose={() => setGeneratedInvite(null)} workspaceId={auth.current_workspace_id} />}
      {confirmation && <ConfirmDialog confirmation={confirmation} busy={actionBusy} onClose={() => !actionBusy && setConfirmation(null)} onConfirm={() => void runAction(confirmation.action, confirmation.confirmLabel.endsWith("?") ? "Action completed." : `${confirmation.confirmLabel.replace("?", "")} completed.`)} />}
      {toast && <div className="team-toast" role="status">✓ {toast}</div>}
    </div>
  );
}

function MemberDetail({
  member,
  actor,
  canManage,
  connections,
  collapsed,
  onToggleCollapsed,
  onSaveRole,
  onSavePermissions,
  onSaveAccessPolicy,
  onSaveSlack,
  onSuspend,
  onReactivate,
  onRemove,
}: {
  member: WorkspaceMember | null;
  actor: WorkspaceMember | undefined;
  canManage: boolean;
  connections: ProviderConnection[];
  collapsed: boolean;
  onToggleCollapsed: () => void;
  onSaveRole: (role: string) => Promise<void>;
  onSavePermissions: (overrides: PermissionOverrides) => Promise<void>;
  onSaveAccessPolicy: (policy: AccessPolicy) => Promise<void>;
  onSaveSlack: (slackId: string) => Promise<void>;
  onSuspend: () => void;
  onReactivate: () => void;
  onRemove: () => void;
}) {
  const [role, setRole] = useState(member?.role ?? "viewer");
  const [overrides, setOverrides] = useState<PermissionOverrides>(member?.permission_overrides ?? emptyOverrides());
  const [accessPolicy, setAccessPolicy] = useState<AccessPolicy>(member?.access_policy ?? emptyAccessPolicy());
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState(false);
  const [slackId, setSlackId] = useState(member?.slack_member_id ?? "");
  useEffect(() => {
    setRole(member?.role ?? "viewer");
    setOverrides(member?.permission_overrides ?? emptyOverrides());
    setAccessPolicy(member?.access_policy ?? emptyAccessPolicy());
    setEditing(false);
    setSlackId(member?.slack_member_id ?? "");
  }, [member]);
  if (!member) return <aside className="team-detail panel"><div className="team-detail-empty"><span className="eyebrow">User details</span><h2>Select a user</h2><p>Choose a person from the roster to edit their role and permissions or remove their access.</p></div></aside>;
  const selectedMember = member;
  function cancelEditing() {
    setRole(selectedMember.role);
    setOverrides(selectedMember.permission_overrides);
    setAccessPolicy(selectedMember.access_policy);
    setEditing(false);
  }
  const isOwner = member.role === "owner";
  const isSelf = actor?.id === member.id;
  const canEdit = canManage && !isOwner && !isSelf;
  const editable = canEdit && editing;
  const effective = new Set(member.effective_permissions);
  async function saveRole() { setSaving(true); try { await onSaveRole(role); } finally { setSaving(false); } }
  async function savePermissions() { setSaving(true); try { await onSavePermissions(overrides); } finally { setSaving(false); } }
  async function saveAccessPolicy() { setSaving(true); try { await onSaveAccessPolicy(accessPolicy); } finally { setSaving(false); } }
  return (
    <aside className="team-detail panel">
      <div className="team-detail-head">
        <span className="member-avatar large">{member.display_name.slice(0, 1).toUpperCase()}</span>
        <div><span className="eyebrow">User details</span><h2>{member.display_name}</h2><p>{member.email}</p></div>
        <button
          type="button"
          className="member-details-toggle"
          aria-expanded={!collapsed}
          aria-label={collapsed ? `Show details for ${member.display_name}` : `Collapse details for ${member.display_name}`}
          onClick={onToggleCollapsed}
        >{collapsed ? "Show details +" : "Collapse −"}</button>
      </div>
      <div className="team-detail-badges"><RolePill role={member.role} /><MemberStatus status={member.status} />{isSelf && <span className="you-badge">You</span>}</div>
      {!collapsed && <>
        <div className="member-detail-actions">
          {canEdit && <button className="secondary-button" onClick={() => editing ? cancelEditing() : setEditing(true)}>{editing ? "Cancel edit" : "Edit access"}</button>}
          {canManage && !isOwner && !isSelf && <button className="button-quiet danger-text" onClick={onRemove}>Remove from workspace</button>}
        </div>
        {isOwner && <div className="owner-protected"><b>Ownership protected</b><span>The workspace owner cannot be suspended, revoked or removed. Ownership transfer is not available in this phase.</span></div>}
        {isSelf && !isOwner && <div className="owner-protected"><b>Your own access is protected</b><span>Select another user to edit their role or permissions. Admins can manage other Admins, but cannot change their own access here.</span></div>}
        {!canManage && !isOwner && !isSelf && <div className="owner-protected"><b>Read-only access</b><span>Your role does not include permission to manage workspace users.</span></div>}
        <div className="team-detail-facts">
          <div><span>Invited</span><b>{formatDate(member.invited_at)}</b></div>
          <div><span>Joined</span><b>{formatDate(member.accepted_at)}</b></div>
          <div><span>Last updated</span><b>{formatDate(member.updated_at)}</b></div>
        </div>
        <div className="detail-editor-section">
          <div className="section-heading"><div><span className="eyebrow">Personal win notifications</span><h3>Slack member link</h3></div></div>
          <p className="role-edit-help">Copy this person's member ID from their Slack profile (More › Copy member ID). Link it after Slack is verified in Integrations. New lead assignments will be sent to this person as a direct Slack message; wins can also notify the team channel.</p>
          <label className="follow-up-field">Slack member ID<input value={slackId} disabled={!canManage || saving} placeholder="U0123456789" maxLength={64} onChange={(event) => setSlackId(event.target.value)} onBlur={(event) => setSlackId(event.target.value.trim().toUpperCase())} /></label>
          {member.slack_team_id && <small className="role-edit-help">Linked Slack workspace: {member.slack_team_id}</small>}
          {canManage && slackId !== (member.slack_member_id ?? "") && <button className="secondary-button" disabled={saving || (!!slackId && !/^[UW][A-Z0-9]{8,63}$/.test(slackId))} onClick={async () => { setSaving(true); try { await onSaveSlack(slackId); } finally { setSaving(false); } }}>{saving ? "Saving…" : slackId ? "Save Slack link" : "Remove Slack link"}</button>}
        </div>
        <div className="detail-editor-section">
          <div className="section-heading">
            <div><span className="eyebrow">Role defaults</span><h3>Role</h3></div>
            {editable && role !== member.role && <button className="button-quiet" disabled={saving} onClick={() => setRole(member.role)}>Reset</button>}
          </div>
          <select value={role} disabled={!editable || saving} onChange={(event) => setRole(event.target.value)}>
            {Object.entries(ROLE_LABELS).filter(([id]) => id !== "owner").map(([id, label]) => <option value={id} key={id}>{label}</option>)}
          </select>
          {editable && <small className="role-edit-help">Admins can assign the Admin role to another user. Owner access stays protected.</small>}
          {editable && role !== member.role && <button className="secondary-button" disabled={saving} onClick={() => void saveRole()}>{saving ? "Saving…" : "Save role"}</button>}
        </div>
        <div className="detail-editor-section">
          <div className="section-heading">
            <div><span className="eyebrow">Role + exceptions</span><h3>Effective permissions</h3></div>
            {editable && (overrides.grants.length > 0 || overrides.denies.length > 0) && <button className="button-quiet" disabled={saving} onClick={() => setOverrides(emptyOverrides())}>Reset to role defaults</button>}
          </div>
          <PermissionEditor overrides={overrides} effective={effective} readOnly={!editable || saving} onChange={setOverrides} />
          {editable && JSON.stringify(overrides) !== JSON.stringify(member.permission_overrides) && <button className="secondary-button" disabled={saving} onClick={() => void savePermissions()}>{saving ? "Saving…" : "Save permissions"}</button>}
        </div>
        <div className="detail-editor-section">
          <div className="section-heading">
            <div><span className="eyebrow">Data boundaries</span><h3>Record access scopes</h3></div>
            {editable && Object.keys(accessPolicy.scopes).length > 0 && <button className="button-quiet" disabled={saving} onClick={() => setAccessPolicy(emptyAccessPolicy())}>Reset to role defaults</button>}
          </div>
          <AccessScopeEditor policy={accessPolicy} readOnly={!editable || saving} onChange={setAccessPolicy} />
          {editable && JSON.stringify(accessPolicy) !== JSON.stringify(member.access_policy) && <button className="secondary-button" disabled={saving} onClick={() => void saveAccessPolicy()}>{saving ? "Saving…" : "Save record scopes"}</button>}
        </div>
        <div className="detail-editor-section">
          <div className="section-heading">
            <div><span className="eyebrow">Connected account delegation</span><h3>Shared integrations</h3></div>
          </div>
          <ConnectionAccessEditor
            memberId={member.id}
            connections={connections}
            editable={canManage && !isOwner && !isSelf}
          />
        </div>
        {canManage && !isOwner && !isSelf && <div className="member-danger-actions">
          {member.status === "suspended"
            ? <button className="button-quiet" onClick={onReactivate}>Reactivate member</button>
            : member.status === "active" && <button className="button-quiet danger-text" onClick={onSuspend}>Suspend member</button>}
        </div>}
      </>}
    </aside>
  );
}

function InviteDialog({
  email,
  role,
  overrides,
  accessPolicy,
  busy,
  onEmail,
  onRole,
  onOverrides,
  onAccessPolicy,
  onClose,
  onSubmit,
}: {
  email: string;
  role: string;
  overrides: PermissionOverrides;
  accessPolicy: AccessPolicy;
  busy: boolean;
  onEmail: (value: string) => void;
  onRole: (value: string) => void;
  onOverrides: (value: PermissionOverrides) => void;
  onAccessPolicy: (value: AccessPolicy) => void;
  onClose: () => void;
  onSubmit: (event: React.FormEvent) => void;
}) {
  const [step, setStep] = useState<"details" | "review">("details");
  const roleInfo = ROLE_OPTIONS.find((item) => item.id === role) ?? ROLE_OPTIONS[0];
  function review(event: React.FormEvent) {
    event.preventDefault();
    setStep("review");
  }
  return <OperationsDialog title="Invite team member" className="team-modal panel" showHeader={false} onClose={() => { if (!busy) onClose(); }}><div className="team-modal-head"><div><span className="eyebrow">Add workspace staff · {step === "details" ? "01 Details" : "02 Review"}</span><h2 id="invite-title">Invite team member</h2><p>{step === "details" ? "Choose an email, role, record scope and deliberate permission exceptions." : "Review the access package before creating the pending invitation."}</p></div><button className="modal-close" onClick={onClose} aria-label="Close invitation dialog">×</button></div><form onSubmit={step === "details" ? review : onSubmit}>{step === "details" ? <><label className="team-field"><span>Work email</span><input required type="email" value={email} onChange={(event) => onEmail(event.target.value)} placeholder="name@company.com" autoFocus /></label><label className="team-field"><span>Workspace role</span><select value={role} onChange={(event) => onRole(event.target.value)}>{ROLE_OPTIONS.map((item) => <option value={item.id} key={item.id}>{item.label}</option>)}</select><small>{roleInfo.description}</small></label><div className="invite-permission-custom"><div className="section-heading"><div><span className="eyebrow">Optional customization</span><h3>Record scopes</h3></div></div><AccessScopeEditor policy={accessPolicy} onChange={onAccessPolicy} /><div className="section-heading"><div><span className="eyebrow">Optional customization</span><h3>Permission overrides</h3></div><span className="panel-action">Tap to cycle: grant · deny · reset</span></div><PermissionEditor overrides={overrides} effective={defaultPermissionsForRole(role)} onChange={onOverrides} /></div><div className="team-modal-actions"><button type="button" className="button-quiet" onClick={onClose}>Cancel</button><button className="primary-button">Review invitation ›</button></div></> : <><div className="invite-review-card"><span className="member-avatar large">{email.slice(0, 1).toUpperCase()}</span><div><b>{email}</b><RolePill role={role} /><p>{roleInfo.description}</p></div></div><div className="invite-review-facts"><span>Custom grants <b>{overrides.grants.length}</b></span><span>Explicit denies <b>{overrides.denies.length}</b></span><span>Scope overrides <b>{Object.keys(accessPolicy.scopes).length}</b></span><span>Access <b>One-time code</b></span></div><div className="notice">The code is shown once after creation. Share it privately with the invited staff member; resending invalidates the previous code.</div><div className="team-modal-actions"><button type="button" className="button-quiet" onClick={() => setStep("details")} disabled={busy}>‹ Edit details</button><button className="primary-button" disabled={busy}>{busy ? "Creating…" : "Create access code"}</button></div></>}</form></OperationsDialog>;
}

function InviteCodeDialog({ invitation, workspaceId, onClose }: { invitation: WorkspaceInvitation; workspaceId: string; onClose: () => void }) {
  const code = invitation.access_code ?? "";
  async function copy(value: string) {
    await navigator.clipboard?.writeText(value);
  }
  return <OperationsDialog title="Access code ready" className="team-modal panel" showHeader={false} onClose={onClose}><div className="team-modal-head"><div><span className="eyebrow">Staff portal access</span><h2 id="access-code-title">Access code ready</h2><p>Send these details privately to {invitation.email}. The code expires {formatDate(invitation.expires_at)} and can be used once.</p></div><button className="modal-close" onClick={onClose} aria-label="Close access code dialog">×</button></div><div className="access-code-fields"><label>Workspace ID<div><code>{workspaceId}</code><button type="button" className="button-quiet" onClick={() => void copy(workspaceId)}>Copy</button></div></label><label>Staff access code<div><code>{code}</code><button type="button" className="button-quiet" onClick={() => void copy(code)}>Copy</button></div></label></div><div className="notice">Staff select <b>Staff access</b> on the portal, enter the workspace ID, this code and their invited email, then enter their name. New accounts must create a password of at least 10 characters; their email is their username for future sign-ins. Existing accounts keep their current password. Admins and owners are the only roles that can create or rotate codes.</div><div className="team-modal-actions"><button className="primary-button" onClick={onClose}>Done</button></div></OperationsDialog>;
}

function ConfirmDialog({ confirmation, busy, onClose, onConfirm }: { confirmation: Confirmation; busy: boolean; onClose: () => void; onConfirm: () => void }) {
  return <OperationsDialog title={confirmation.title} className="team-modal confirm-modal panel" showHeader={false} onClose={() => { if (!busy) onClose(); }}><div className="team-modal-head"><div><span className="eyebrow">Confirm sensitive action</span><h2 id="confirm-title">{confirmation.title}</h2><p>{confirmation.detail}</p></div><button className="modal-close" onClick={onClose} disabled={busy} aria-label="Close confirmation dialog">×</button></div><div className="team-modal-actions"><button className="button-quiet" onClick={onClose} disabled={busy}>Cancel</button><button className={confirmation.danger ? "danger-button" : "primary-button"} onClick={onConfirm} disabled={busy}>{busy ? "Working…" : confirmation.confirmLabel}</button></div></OperationsDialog>;
}

function TeamListSkeleton() {
  return <div className="team-list-skeleton" aria-busy="true" aria-label="Loading team members">{[1, 2, 3, 4].map((item) => <i key={item} />)}</div>;
}
