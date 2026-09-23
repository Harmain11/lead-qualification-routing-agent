import { useCallback, useEffect, useMemo, useState } from "react";

import {
  inviteMember,
  listInvitations,
  listMemberAuditEvents,
  listMembers,
  reactivateMember,
  removeMember,
  resendInvitation,
  revokeInvitation,
  suspendMember,
  updateMemberPermissions,
  updateMemberRole,
} from "../api";
import { can, defaultPermissionsForRole, PERMISSION_GROUPS, ROLE_LABELS, ROLE_OPTIONS, roleLabel, statusLabel } from "../permissions";
import type {
  AuthSession,
  PermissionOverrides,
  WorkspaceAuditEvent,
  WorkspaceInvitation,
  WorkspaceMember,
} from "../types";

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

function friendlyError(error: unknown) {
  const message = error instanceof Error ? error.message : "The request could not be completed.";
  if (message.includes("permission required")) return "You do not have permission to do that in this workspace.";
  if (message.includes("already a workspace member")) return "That person already belongs to this workspace.";
  if (message.includes("already been used")) return "This invitation has already been accepted or revoked.";
  if (message.includes("expired")) return "This invitation has expired. Resend it to create a fresh invitation.";
  if (message.includes("ownership") || message.includes("protected")) return "Workspace ownership is protected and cannot be changed here.";
  return message;
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
  const next = current === "none" || current === "inherited" ? "grant" : current === "grant" ? "deny" : "none";
  return {
    grants: next === "grant"
      ? [...overrides.grants.filter((item) => item !== permission), permission].sort()
      : overrides.grants.filter((item) => item !== permission),
    denies: next === "deny"
      ? [...overrides.denies.filter((item) => item !== permission), permission].sort()
      : overrides.denies.filter((item) => item !== permission),
  };
}

function PermissionEditor({ overrides, effective, readOnly, onChange }: PermissionEditorProps) {
  return (
    <div className="permission-editor">
      <div className="permission-legend" aria-label="Permission state legend">
        <span><i className="permission-dot inherited" /> Inherited</span>
        <span><i className="permission-dot grant" /> Custom grant</span>
        <span><i className="permission-dot deny" /> Explicit deny</span>
      </div>
      <div className="permission-groups">
        {PERMISSION_GROUPS.map((group) => (
          <section className="permission-group" key={group.label}>
            <h3>{group.label}</h3>
            <div>
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
            </div>
          </section>
        ))}
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
  return <li><span className="audit-marker" /><div><b>{text}</b><small>{formatDate(event.created_at)}</small></div></li>;
}

export function TeamView({ auth, workspaceName, onBack }: TeamViewProps) {
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [invitations, setInvitations] = useState<WorkspaceInvitation[]>([]);
  const [audit, setAudit] = useState<WorkspaceAuditEvent[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState("sales_rep");
  const [inviteOverrides, setInviteOverrides] = useState<PermissionOverrides>(emptyOverrides);
  const [inviteBusy, setInviteBusy] = useState(false);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [actionBusy, setActionBusy] = useState(false);

  const load = useCallback(async (signal?: AbortSignal, preserve = false) => {
    if (preserve) setRefreshing(true); else setLoading(true);
    setError(null);
    try {
      const [nextMembers, nextInvitations, nextAudit] = await Promise.all([
        listMembers(signal),
        listInvitations(signal),
        listMemberAuditEvents(signal),
      ]);
      if (signal?.aborted) return;
      setMembers(nextMembers);
      setInvitations(nextInvitations);
      setAudit(nextAudit);
      setSelectedId((current) => current && nextMembers.some((member) => member.id === current) ? current : nextMembers[0]?.id ?? null);
    } catch (requestError) {
      if (!signal?.aborted) setError(friendlyError(requestError));
    } finally {
      if (!signal?.aborted) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, []);

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
      await inviteMember({ email: inviteEmail, role: inviteRole, permission_overrides: inviteOverrides });
      await load(undefined, true);
      setInviteOpen(false);
      setInviteEmail("");
      setInviteOverrides(emptyOverrides());
      showToast("Invitation created and waiting for delivery.");
    } catch (requestError) {
      setError(friendlyError(requestError));
    } finally {
      setInviteBusy(false);
    }
  }

  const requestConfirmation = (next: Confirmation) => setConfirmation(next);

  return (
    <div className="page-view operational-page team-page">
      <header className="page-hero compact-hero team-hero">
        <div>
          <button className="team-back" onClick={onBack}>← Settings</button>
          <span className="eyebrow">Workspace administration</span>
          <h1>Team &amp; <em>permissions.</em></h1>
          <p>Share <strong>{workspaceName}</strong> with the people who move revenue forward. Role defaults stay clear, and every exception is visible.</p>
        </div>
        <div className="team-context-card"><span>Editing workspace</span><strong>{workspaceName}</strong><small>{auth.current_workspace_id}</small><i>Permissions are workspace-specific</i></div>
      </header>

      {error && <div className="team-alert" role="alert"><span>{error}</span><button onClick={() => setError(null)} aria-label="Dismiss message">×</button></div>}
      {!canView && !loading && <div className="team-denied panel"><span className="eyebrow">Members view</span><h2>Team visibility is restricted</h2><p>Your current workspace permissions do not include <code>members.view</code>. Ask an owner or admin to grant access.</p></div>}
      {canView && (
        <>
          <section className="team-summary-grid">
            <div className="team-summary panel"><span className="eyebrow">Workspace roster</span><strong>{members.length}</strong><small>{members.filter((member) => member.status === "active").length} active members</small></div>
            <div className="team-summary panel"><span className="eyebrow">Pending invitations</span><strong>{invitations.filter((invitation) => invitation.status === "pending").length}</strong><small>Delivery is represented as pending</small></div>
            <div className="team-summary panel"><span className="eyebrow">Your access</span><strong>{actor ? roleLabel(actor.role) : "Resolving…"}</strong><small>{actorPermissions.size} effective permissions</small></div>
          </section>
          <section className="team-layout">
            <div className="team-main-column">
              <div className="panel team-members-panel">
                <div className="section-heading"><div><span className="eyebrow">People with access</span><h2>Workspace members</h2></div><div className="team-heading-actions">{refreshing && <span className="inline-loading">Refreshing</span>}{canInvite && <button className="primary-button" onClick={() => setInviteOpen(true)}>+ Invite team member</button>}</div></div>
                {loading ? <TeamListSkeleton /> : members.length ? <div className="team-member-list">{members.map((member) => <button type="button" className={`team-member-row ${selected?.id === member.id ? "selected" : ""}`} key={member.id} onClick={() => setSelectedId(member.id)}><span className="member-avatar">{member.display_name.slice(0, 1).toUpperCase()}</span><span className="member-identity"><b>{member.display_name}{member.user_id === auth.user.id && <em>You</em>}</b><small>{member.email}</small></span><RolePill role={member.role} /><MemberStatus status={member.status} /><span className="member-permission-count">{member.effective_permissions.length} permissions</span><i className="member-chevron">→</i></button>)}</div> : <div className="empty-state"><i /><b>No members yet</b><span>Invite the first staff member to begin sharing this workspace.</span></div>}
              </div>
              <div className="panel team-invitations-panel">
                <div className="section-heading"><div><span className="eyebrow">Pending access</span><h2>Invitations</h2></div><span className="panel-action">No invitation tokens shown</span></div>
                {loading ? <div className="team-invite-skeleton" /> : invitations.filter((invitation) => invitation.status === "pending").length ? <div className="invitation-list">{invitations.filter((invitation) => invitation.status === "pending").map((invitation) => <div className="invitation-row" key={invitation.id}><span className="invitation-icon">✉</span><span><b>{invitation.email}</b><small>{roleLabel(invitation.role)} · Expires {formatDate(invitation.expires_at)}</small></span><span className="invitation-status">Pending delivery</span>{canManage && <span className="invitation-actions"><button onClick={() => requestConfirmation({ title: "Resend invitation?", detail: `A fresh invitation will be created for ${invitation.email}.`, confirmLabel: "Resend invitation", action: async () => { await resendInvitation(invitation.id); } })}>Resend</button><button className="danger-text" onClick={() => requestConfirmation({ title: "Revoke invitation?", detail: `${invitation.email} will no longer be able to join from this invitation.`, confirmLabel: "Revoke invitation", danger: true, action: async () => { await revokeInvitation(invitation.id); } })}>Revoke</button></span>}</div>)}</div> : <div className="empty-state compact-empty"><b>No pending invitations</b><span>Invitations will stay here until accepted, expired or revoked.</span></div>}
              </div>
              <div className="panel team-audit-panel"><div className="section-heading"><div><span className="eyebrow">Durable administration trail</span><h2>Recent activity</h2></div><span className="panel-action">Last {Math.min(audit.length, 6)} events</span></div>{audit.length ? <ul className="team-audit-list">{audit.slice(0, 6).map((event) => <AuditLine key={event.id} event={event} members={members} />)}</ul> : <div className="compact-empty"><span>No administration activity recorded yet.</span></div>}</div>
            </div>
            <MemberDetail member={selected} actor={actor} canManage={canManage} onSaveRole={async (role) => { if (!selected) return; await runAction(async () => { const next = await updateMemberRole(selected.id, role); setMembers((current) => current.map((item) => item.id === next.id ? next : item)); }, "Role updated."); }} onSavePermissions={async (overrides) => { if (!selected) return; await runAction(async () => { const next = await updateMemberPermissions(selected.id, overrides); setMembers((current) => current.map((item) => item.id === next.id ? next : item)); }, "Permissions updated."); }} onSuspend={() => selected && requestConfirmation({ title: "Suspend member?", detail: `${selected.display_name} will lose workspace access until reactivated.`, confirmLabel: "Suspend member", danger: true, action: async () => { await suspendMember(selected.id); } })} onReactivate={() => selected && runAction(() => reactivateMember(selected.id).then(() => undefined), "Member reactivated.")} onRemove={() => selected && requestConfirmation({ title: "Remove access?", detail: `${selected.display_name} will be revoked from this workspace. Existing sessions will stop authorizing this workspace.`, confirmLabel: "Remove access", danger: true, action: async () => { await removeMember(selected.id); } })} />
          </section>
        </>
      )}
      {inviteOpen && <InviteDialog email={inviteEmail} role={inviteRole} overrides={inviteOverrides} busy={inviteBusy} onEmail={setInviteEmail} onRole={setInviteRole} onOverrides={setInviteOverrides} onClose={() => setInviteOpen(false)} onSubmit={submitInvitation} />}
      {confirmation && <ConfirmDialog confirmation={confirmation} busy={actionBusy} onClose={() => !actionBusy && setConfirmation(null)} onConfirm={() => void runAction(confirmation.action, confirmation.confirmLabel.endsWith("?") ? "Action completed." : `${confirmation.confirmLabel.replace("?", "")} completed.`)} />}
      {toast && <div className="team-toast" role="status">✓ {toast}</div>}
    </div>
  );
}

function MemberDetail({
  member,
  actor,
  canManage,
  onSaveRole,
  onSavePermissions,
  onSuspend,
  onReactivate,
  onRemove,
}: {
  member: WorkspaceMember | null;
  actor: WorkspaceMember | undefined;
  canManage: boolean;
  onSaveRole: (role: string) => Promise<void>;
  onSavePermissions: (overrides: PermissionOverrides) => Promise<void>;
  onSuspend: () => void;
  onReactivate: () => void;
  onRemove: () => void;
}) {
  const [role, setRole] = useState(member?.role ?? "viewer");
  const [overrides, setOverrides] = useState<PermissionOverrides>(member?.permission_overrides ?? emptyOverrides());
  const [saving, setSaving] = useState(false);
  useEffect(() => { setRole(member?.role ?? "viewer"); setOverrides(member?.permission_overrides ?? emptyOverrides()); }, [member]);
  if (!member) return <aside className="team-detail panel"><div className="team-detail-empty"><span className="eyebrow">Member detail</span><h2>Select a member</h2><p>Choose a person from the roster to inspect their role, effective permissions and access history.</p></div></aside>;
  const isOwner = member.role === "owner";
  const isSelf = actor?.id === member.id;
  const editable = canManage && !isOwner && !isSelf;
  const effective = new Set(member.effective_permissions);
  async function saveRole() { setSaving(true); try { await onSaveRole(role); } finally { setSaving(false); } }
  async function savePermissions() { setSaving(true); try { await onSavePermissions(overrides); } finally { setSaving(false); } }
  return <aside className="team-detail panel"><div className="team-detail-head"><span className="member-avatar large">{member.display_name.slice(0, 1).toUpperCase()}</span><div><span className="eyebrow">Member detail</span><h2>{member.display_name}</h2><p>{member.email}</p></div></div><div className="team-detail-badges"><RolePill role={member.role} /><MemberStatus status={member.status} />{isSelf && <span className="you-badge">You</span>}</div>{isOwner && <div className="owner-protected"><b>Ownership protected</b><span>The workspace owner cannot be suspended, revoked or removed. Ownership transfer is not available in this phase.</span></div>}<div className="team-detail-facts"><div><span>Invited</span><b>{formatDate(member.invited_at)}</b></div><div><span>Joined</span><b>{formatDate(member.accepted_at)}</b></div><div><span>Last updated</span><b>{formatDate(member.updated_at)}</b></div></div><div className="detail-editor-section"><div className="section-heading"><div><span className="eyebrow">Role defaults</span><h3>Role</h3></div>{editable && role !== member.role && <button className="button-quiet" disabled={saving} onClick={() => setRole(member.role)}>Reset</button>}</div><select value={role} disabled={!editable || saving} onChange={(event) => setRole(event.target.value)}>{Object.entries(ROLE_LABELS).filter(([id]) => id !== "owner").map(([id, label]) => <option value={id} key={id}>{label}</option>)}</select>{editable && role !== member.role && <button className="secondary-button" disabled={saving} onClick={() => void saveRole()}>{saving ? "Saving…" : "Save role"}</button>}</div><div className="detail-editor-section"><div className="section-heading"><div><span className="eyebrow">Role + exceptions</span><h3>Effective permissions</h3></div>{editable && (overrides.grants.length > 0 || overrides.denies.length > 0) && <button className="button-quiet" disabled={saving} onClick={() => setOverrides(emptyOverrides())}>Reset to role defaults</button>}</div><PermissionEditor overrides={overrides} effective={effective} readOnly={!editable || saving} onChange={setOverrides} />{editable && JSON.stringify(overrides) !== JSON.stringify(member.permission_overrides) && <button className="secondary-button" disabled={saving} onClick={() => void savePermissions()}>{saving ? "Saving…" : "Save permissions"}</button>}</div>{canManage && !isOwner && !isSelf && <div className="member-danger-actions">{member.status === "suspended" ? <button className="button-quiet" onClick={onReactivate}>Reactivate member</button> : member.status === "active" && <button className="button-quiet danger-text" onClick={onSuspend}>Suspend member</button>}<button className="button-quiet danger-text" onClick={onRemove}>Remove access</button></div>}</aside>;
}

function InviteDialog({
  email,
  role,
  overrides,
  busy,
  onEmail,
  onRole,
  onOverrides,
  onClose,
  onSubmit,
}: {
  email: string;
  role: string;
  overrides: PermissionOverrides;
  busy: boolean;
  onEmail: (value: string) => void;
  onRole: (value: string) => void;
  onOverrides: (value: PermissionOverrides) => void;
  onClose: () => void;
  onSubmit: (event: React.FormEvent) => void;
}) {
  const [step, setStep] = useState<"details" | "review">("details");
  const roleInfo = ROLE_OPTIONS.find((item) => item.id === role) ?? ROLE_OPTIONS[0];
  function review(event: React.FormEvent) {
    event.preventDefault();
    setStep("review");
  }
  return <div className="team-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="team-modal panel" role="dialog" aria-modal="true" aria-labelledby="invite-title"><div className="team-modal-head"><div><span className="eyebrow">Add workspace staff · {step === "details" ? "01 Details" : "02 Review"}</span><h2 id="invite-title">Invite team member</h2><p>{step === "details" ? "Choose an email, role and any deliberate permission exceptions." : "Review the access package before creating the pending invitation."}</p></div><button className="modal-close" onClick={onClose} aria-label="Close invitation dialog">×</button></div><form onSubmit={step === "details" ? review : onSubmit}>{step === "details" ? <><label className="team-field"><span>Work email</span><input required type="email" value={email} onChange={(event) => onEmail(event.target.value)} placeholder="name@company.com" autoFocus /></label><label className="team-field"><span>Workspace role</span><select value={role} onChange={(event) => onRole(event.target.value)}>{ROLE_OPTIONS.map((item) => <option value={item.id} key={item.id}>{item.label}</option>)}</select><small>{roleInfo.description}</small></label><div className="invite-permission-custom"><div className="section-heading"><div><span className="eyebrow">Optional customization</span><h3>Permission overrides</h3></div><span className="panel-action">Tap to cycle: grant · deny · reset</span></div><PermissionEditor overrides={overrides} effective={defaultPermissionsForRole(role)} onChange={onOverrides} /></div><div className="team-modal-actions"><button type="button" className="button-quiet" onClick={onClose}>Cancel</button><button className="primary-button">Review invitation →</button></div></> : <><div className="invite-review-card"><span className="member-avatar large">{email.slice(0, 1).toUpperCase()}</span><div><b>{email}</b><RolePill role={role} /><p>{roleInfo.description}</p></div></div><div className="invite-review-facts"><span>Custom grants <b>{overrides.grants.length}</b></span><span>Explicit denies <b>{overrides.denies.length}</b></span><span>Delivery <b>Pending</b></span></div><div className="notice">No invitation token is shown in the UI. Email delivery remains a pending integration.</div><div className="team-modal-actions"><button type="button" className="button-quiet" onClick={() => setStep("details")} disabled={busy}>← Edit details</button><button className="primary-button" disabled={busy}>{busy ? "Creating…" : "Create invitation"}</button></div></>}</form></section></div>;
}

function ConfirmDialog({ confirmation, busy, onClose, onConfirm }: { confirmation: Confirmation; busy: boolean; onClose: () => void; onConfirm: () => void }) {
  return <div className="team-modal-backdrop" role="presentation"><section className="team-modal confirm-modal panel" role="dialog" aria-modal="true" aria-labelledby="confirm-title"><div className="team-modal-head"><div><span className="eyebrow">Confirm sensitive action</span><h2 id="confirm-title">{confirmation.title}</h2><p>{confirmation.detail}</p></div><button className="modal-close" onClick={onClose} disabled={busy} aria-label="Close confirmation dialog">×</button></div><div className="team-modal-actions"><button className="button-quiet" onClick={onClose} disabled={busy}>Cancel</button><button className={confirmation.danger ? "danger-button" : "primary-button"} onClick={onConfirm} disabled={busy}>{busy ? "Working…" : confirmation.confirmLabel}</button></div></section></div>;
}

function TeamListSkeleton() {
  return <div className="team-list-skeleton" aria-busy="true" aria-label="Loading team members">{[1, 2, 3, 4].map((item) => <i key={item} />)}</div>;
}
