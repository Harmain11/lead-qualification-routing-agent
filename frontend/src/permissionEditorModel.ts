import type {
  PermissionOverrides,
  PermissionRegistryCategory,
} from "./types";

export type PermissionDecision = "inherit" | "grant" | "deny";
export type PermissionDisplayState =
  | "inherited"
  | "granted"
  | "denied"
  | "exception-grant"
  | "exception-deny";

export function providerPermissionId(provider: string, action: string) {
  return `integrations.${provider}.${action}`;
}

export function permissionDisplayState(
  permission: string,
  overrides: PermissionOverrides,
  inherited: ReadonlySet<string>,
): PermissionDisplayState {
  if (overrides.denies.includes(permission)) return "exception-deny";
  if (overrides.grants.includes(permission)) return "exception-grant";
  return inherited.has(permission) ? "inherited" : "denied";
}

export function permissionIsGranted(state: PermissionDisplayState) {
  return state === "inherited" || state === "granted" || state === "exception-grant";
}

export function setPermissionDecision(
  overrides: PermissionOverrides,
  permission: string,
  decision: PermissionDecision,
): PermissionOverrides {
  const grants = new Set(overrides.grants);
  const denies = new Set(overrides.denies);
  grants.delete(permission);
  denies.delete(permission);
  if (decision === "grant") grants.add(permission);
  if (decision === "deny") denies.add(permission);
  return {
    grants: [...grants].sort(),
    denies: [...denies].sort(),
  };
}

export function applyProviderDecision(
  overrides: PermissionOverrides,
  providers: readonly string[],
  action: string,
  decision: PermissionDecision,
): PermissionOverrides {
  return providers.reduce(
    (next, provider) => setPermissionDecision(
      next,
      providerPermissionId(provider, action),
      decision,
    ),
    overrides,
  );
}

export function providerActionSummary(
  overrides: PermissionOverrides,
  inherited: ReadonlySet<string>,
  providers: readonly string[],
  action: string,
): PermissionDisplayState | "mixed" | "unselected" {
  if (!providers.length) return "unselected";
  const states = new Set(providers.map((provider) => permissionDisplayState(
    providerPermissionId(provider, action),
    overrides,
    inherited,
  )));
  return states.size === 1 ? [...states][0] : "mixed";
}

export function categoryAccessSummary(
  category: PermissionRegistryCategory,
  overrides: PermissionOverrides,
  inherited: ReadonlySet<string>,
) {
  const activePermissions = category.feature_permissions.filter((permission) =>
    permissionIsGranted(permissionDisplayState(permission.id, overrides, inherited)),
  ).length;
  const activeProviders = category.providers.filter((provider) =>
    category.provider_actions.some((action) => permissionIsGranted(permissionDisplayState(
      providerPermissionId(provider.id, action.id),
      overrides,
      inherited,
    ))),
  ).length;
  return {
    activePermissions,
    totalPermissions: category.feature_permissions.length,
    activeProviders,
    totalProviders: category.providers.length,
  };
}
