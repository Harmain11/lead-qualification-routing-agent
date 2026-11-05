export type PermissionGroup = {
  label: string;
  permissions: Array<{ id: string; label: string }>;
};

export const PERMISSION_GROUPS: PermissionGroup[] = [
  {
    label: "Leads",
    permissions: [
      { id: "leads.view", label: "View leads" },
      { id: "leads.edit", label: "Edit leads" },
      { id: "leads.export", label: "Export leads" },
      { id: "leads.reassign", label: "Reassign leads" },
    ],
  },
  {
    label: "Collaboration",
    permissions: [
      { id: "comments.view", label: "View team notes and comments" },
      { id: "comments.add", label: "Add notes and comments" },
      { id: "activity.view", label: "View lead activity history" },
    ],
  },
  {
    label: "Analytics",
    permissions: [{ id: "analytics.view", label: "View analytics" }],
  },
  {
    label: "Manual review",
    permissions: [
      { id: "manual_review.view", label: "View manual review" },
      { id: "manual_review.manage", label: "Manage manual review" },
    ],
  },
  {
    label: "Routing",
    permissions: [
      { id: "routing.view", label: "View routing" },
      { id: "routing.manage", label: "Manage routing" },
    ],
  },
  {
    label: "Integrations",
    permissions: [
      { id: "integrations.view", label: "View integrations" },
      { id: "integrations.manage", label: "Manage integrations" },
      { id: "integrations.connect", label: "Connect integrations" },
      { id: "integrations.disconnect", label: "Disconnect integrations" },
    ],
  },
  {
    label: "ICP",
    permissions: [
      { id: "icp.view", label: "View ICP" },
      { id: "icp.manage", label: "Manage ICP" },
    ],
  },
  {
    label: "Team",
    permissions: [
      { id: "members.view", label: "View members" },
      { id: "members.invite", label: "Invite members" },
      { id: "members.manage", label: "Manage members" },
    ],
  },
  {
    label: "AI / Agents",
    permissions: [
      { id: "agents.view", label: "View agent activity" },
      { id: "agents.execute", label: "Execute permitted agent actions" },
    ],
  },
  {
    label: "Dialer",
    permissions: [{ id: "dialer.call", label: "Place calls" }],
  },
  {
    label: "CRM",
    permissions: [{ id: "crm.sync", label: "Run permitted CRM synchronization" }],
  },
  {
    label: "Workspace",
    permissions: [{ id: "workspace.manage", label: "Manage workspace" }],
  },
];

export const ROLE_OPTIONS = [
  {
    id: "admin",
    label: "Admin",
    description: "Manage the team, integrations, ICP and most workspace settings.",
  },
  {
    id: "sales_manager",
    label: "Manager",
    description: "Oversee team leads, manual review, reassignment, analytics and exports; routing stays read-only.",
  },
  {
    id: "sales_rep",
    label: "Sales Rep",
    description: "Collaborate on team leads, add notes, use coaching and place permitted calls.",
  },
  {
    id: "viewer",
    label: "Analyst / Viewer",
    description: "Read-only visibility into permitted leads, activity, analytics and team members.",
  },
] as const;

export const ROLE_LABELS: Record<string, string> = {
  owner: "Owner",
  admin: "Admin",
  sales_manager: "Manager",
  sales_rep: "Sales Rep",
  viewer: "Analyst / Viewer",
};

const ALL_PERMISSIONS = PERMISSION_GROUPS.flatMap((group) => group.permissions.map((permission) => permission.id));
export const ROLE_DEFAULT_PERMISSIONS: Record<string, string[]> = {
  owner: ALL_PERMISSIONS,
  admin: ALL_PERMISSIONS,
  sales_manager: [
    "leads.view", "leads.edit", "leads.export", "analytics.view", "manual_review.view",
    "manual_review.manage", "routing.view", "members.view", "agents.view", "agents.execute",
    "dialer.call", "crm.sync", "leads.reassign", "comments.view", "comments.add", "activity.view",
  ],
  sales_rep: ["leads.view", "leads.edit", "analytics.view", "manual_review.view", "routing.view", "agents.view", "agents.execute", "dialer.call", "crm.sync", "members.view", "comments.view", "comments.add", "activity.view"],
  viewer: ["leads.view", "analytics.view", "manual_review.view", "routing.view", "integrations.view", "icp.view", "agents.view", "members.view", "comments.view", "activity.view"],
};

export function roleLabel(role: string) {
  return ROLE_LABELS[role] ?? role.replaceAll("_", " ");
}

export function defaultPermissionsForRole(role: string) {
  return new Set(ROLE_DEFAULT_PERMISSIONS[role] ?? ROLE_DEFAULT_PERMISSIONS.viewer);
}

export function can(permissions: ReadonlySet<string> | Iterable<string>, permission: string) {
  return permissions instanceof Set ? permissions.has(permission) : new Set(permissions).has(permission);
}

export function statusLabel(status: string) {
  return status.charAt(0).toUpperCase() + status.slice(1).replaceAll("_", " ");
}
