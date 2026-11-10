export type PermissionGroup = {
  label: string;
  permissions: Array<{ id: string; label: string }>;
};

const PROVIDERS = [
  "salesforce", "hubspot", "gmail", "outlook", "google_calendar", "calendly",
  "slack", "cloudtalk", "twilio", "aircall", "ringcentral", "vapi", "retell",
  "gohighlevel", "openai", "gemini", "claude", "deepseek", "xai", "linkedin",
  "instagram", "messenger", "whatsapp_business", "clutch",
] as const;

const PROVIDER_ACTIONS = [
  "view", "view_status", "connect_own", "use", "sync", "send", "disconnect_own",
  "manage_own", "manage_shared", "configure", "manage_credentials",
] as const;

const providerLabel = (value: string) => value.split("_").map((part) =>
  part.charAt(0).toUpperCase() + part.slice(1),
).join(" ");

export const PERMISSION_GROUPS: PermissionGroup[] = [
  {
    label: "Leads",
    permissions: [
      { id: "leads.view", label: "View leads" },
      { id: "leads.edit", label: "Edit leads" },
      { id: "leads.export", label: "Export leads" },
      { id: "leads.reassign", label: "Reassign leads" },
      { id: "leads.delete_restore", label: "Delete and restore leads" },
      { id: "leads.share", label: "Share leads" },
      { id: "leads.access.manage", label: "Manage lead access" },
    ],
  },
  {
    label: "Collaboration",
    permissions: [
      { id: "comments.view", label: "View team notes and comments" },
      { id: "comments.add", label: "Add notes and comments" },
      { id: "comments.edit_own", label: "Edit own notes" },
      { id: "comments.edit_shared", label: "Edit shared notes" },
      { id: "comments.delete", label: "Delete notes" },
      { id: "comments.view_private", label: "View private notes" },
      { id: "activity.view", label: "View lead activity history" },
    ],
  },
  {
    label: "Analytics",
    permissions: [
      { id: "analytics.view", label: "View analytics" },
      { id: "analytics.compare", label: "Compare teams and reps" },
      { id: "analytics.sensitive", label: "View sensitive metrics" },
    ],
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
      { id: "agents.configure", label: "Configure agents" },
      { id: "agents.logs.view", label: "View agent logs" },
      { id: "agents.external_actions", label: "Run external actions" },
      { id: "agents.approve_high_impact", label: "Approve high-impact actions" },
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
    permissions: [
      { id: "workspace.manage", label: "Manage workspace" },
      { id: "privacy.manage", label: "Manage privacy policies" },
      { id: "audit.view", label: "View security audit" },
    ],
  },
  {
    label: "Mailbox",
    permissions: [
      { id: "mailbox.view_own", label: "View own mailbox" },
      { id: "mailbox.connect_own", label: "Connect own mailbox" },
      { id: "mailbox.disconnect_own", label: "Disconnect own mailbox" },
      { id: "mailbox.sync_own", label: "Sync own mailbox" },
      { id: "mailbox.send_own", label: "Send from own mailbox" },
      { id: "mailbox.delegate", label: "Delegate shared mailbox" },
    ],
  },
  {
    label: "Calls & recordings",
    permissions: [
      { id: "calls.metadata.view", label: "View call metadata" },
      { id: "calls.transcript.view", label: "View transcripts" },
      { id: "calls.recording.play", label: "Play recordings" },
      { id: "calls.recording.download", label: "Download recordings" },
      { id: "calls.analysis.view", label: "View call analysis" },
    ],
  },
  {
    label: "Sensitive fields",
    permissions: [
      { id: "fields.contact.view", label: "View contact fields" },
      { id: "fields.contact.edit", label: "Edit contact fields" },
      { id: "fields.financial.view", label: "View financial fields" },
      { id: "fields.financial.edit", label: "Edit financial fields" },
      { id: "fields.communications.view", label: "View communication content" },
      { id: "fields.internal.view", label: "View internal fields" },
      { id: "fields.restricted.view", label: "View restricted fields" },
    ],
  },
  ...PROVIDERS.map((provider) => ({
    label: `Integration · ${providerLabel(provider)}`,
    permissions: PROVIDER_ACTIONS.map((action) => ({
      id: `integrations.${provider}.${action}`,
      label: providerLabel(action),
    })),
  })),
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
    description: "Oversee explicitly managed teams, manual review, reassignment and team analytics.",
  },
  {
    id: "sales_rep",
    label: "Sales Rep",
    description: "Work only assigned or explicitly shared leads, own mailbox and approved tools.",
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
    "leads.view", "leads.edit", "analytics.view", "analytics.compare", "manual_review.view",
    "manual_review.manage", "routing.view", "members.view", "agents.view", "agents.execute",
    "dialer.call", "leads.reassign", "comments.view", "comments.add", "comments.edit_own", "activity.view",
    "calls.metadata.view", "calls.transcript.view", "calls.recording.play", "calls.analysis.view",
    "mailbox.view_own", "mailbox.connect_own", "mailbox.disconnect_own", "mailbox.sync_own", "mailbox.send_own",
    "fields.contact.view", "fields.contact.edit", "fields.financial.view", "fields.financial.edit",
    "fields.communications.view", "fields.internal.view",
  ],
  sales_rep: ["leads.view", "leads.edit", "analytics.view", "manual_review.view", "routing.view", "agents.view", "agents.execute", "dialer.call", "members.view", "comments.view", "comments.add", "comments.edit_own", "activity.view", "calls.metadata.view", "calls.transcript.view", "calls.recording.play", "calls.analysis.view", "mailbox.view_own", "mailbox.connect_own", "mailbox.disconnect_own", "mailbox.sync_own", "mailbox.send_own", "fields.contact.view", "fields.contact.edit", "fields.financial.view", "fields.financial.edit", "fields.communications.view"],
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
