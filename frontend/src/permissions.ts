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

export function roleLabel(role: string) {
  return ROLE_LABELS[role] ?? role.replaceAll("_", " ");
}

export function can(permissions: ReadonlySet<string> | Iterable<string>, permission: string) {
  return permissions instanceof Set ? permissions.has(permission) : new Set(permissions).has(permission);
}

export function canSubAssignLeads(
  role: string | null | undefined,
  permissions: ReadonlySet<string> | Iterable<string>,
) {
  // The API accepts the friendly `manager` alias as well as the canonical
  // `sales_manager` role. Normalize both forms here so the main Leads table
  // does not hide the assignment control for managers returned with the
  // friendly label.
  const canonicalRole = role?.trim().toLowerCase().replaceAll("-", " ");
  return Boolean(
    canonicalRole
      && ["owner", "admin", "sales_manager", "sales manager", "manager"].includes(canonicalRole)
      && can(permissions, "leads.reassign"),
  );
}

export function statusLabel(status: string) {
  return status.charAt(0).toUpperCase() + status.slice(1).replaceAll("_", " ");
}

