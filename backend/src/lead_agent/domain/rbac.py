from __future__ import annotations

from enum import StrEnum
from typing import Any


class AccessScope(StrEnum):
    NONE = "none"
    OWN = "own"
    TEAM = "team"
    ALL = "all"
    CUSTOM = "custom"


PROVIDER_KEYS: tuple[str, ...] = (
    "salesforce", "hubspot", "gmail", "outlook", "google_calendar", "calendly",
    "slack", "cloudtalk", "twilio", "aircall", "ringcentral", "vapi", "retell",
    "gohighlevel", "openai", "gemini", "claude", "deepseek", "xai", "linkedin",
    "instagram", "messenger", "whatsapp_business", "clutch",
)
PROVIDER_ACTIONS: tuple[str, ...] = (
    "view", "view_status", "connect_own", "use", "sync", "send", "disconnect_own",
    "manage_own", "manage_shared", "configure", "manage_credentials",
)
PROVIDER_PERMISSIONS: frozenset[str] = frozenset(
    f"integrations.{provider}.{action}"
    for provider in PROVIDER_KEYS
    for action in PROVIDER_ACTIONS
)

PERMISSION_CATALOG: frozenset[str] = frozenset(
    {
        "leads.view",
        "leads.edit",
        "leads.export",
        "leads.reassign",
        "leads.delete_restore",
        "leads.share",
        "leads.access.manage",
        "comments.view",
        "comments.add",
        "comments.edit_own",
        "comments.edit_shared",
        "comments.delete",
        "comments.view_private",
        "activity.view",
        "analytics.view",
        "analytics.compare",
        "analytics.sensitive",
        "manual_review.view",
        "manual_review.manage",
        "routing.view",
        "routing.manage",
        "integrations.view",
        "integrations.manage",
        "integrations.connect",
        "integrations.disconnect",
        "icp.view",
        "icp.manage",
        "members.view",
        "members.invite",
        "members.manage",
        "agents.view",
        "agents.execute",
        "agents.configure",
        "agents.logs.view",
        "agents.external_actions",
        "agents.approve_high_impact",
        "dialer.call",
        "calls.metadata.view",
        "calls.transcript.view",
        "calls.recording.play",
        "calls.recording.download",
        "calls.analysis.view",
        "mailbox.view_own",
        "mailbox.connect_own",
        "mailbox.disconnect_own",
        "mailbox.sync_own",
        "mailbox.send_own",
        "mailbox.delegate",
        "privacy.manage",
        "audit.view",
        "fields.contact.view",
        "fields.contact.edit",
        "fields.financial.view",
        "fields.financial.edit",
        "fields.communications.view",
        "fields.communications.edit",
        "fields.internal.view",
        "fields.internal.edit",
        "fields.restricted.view",
        "fields.restricted.edit",
        "crm.sync",
        "workspace.manage",
    } | PROVIDER_PERMISSIONS
)

ROLE_ALIASES: dict[str, str] = {
    "owner": "owner",
    "admin": "admin",
    "sales manager": "sales_manager",
    "sales_manager": "sales_manager",
    "manager": "sales_manager",
    "sales rep": "sales_rep",
    "sales_rep": "sales_rep",
    "rep": "sales_rep",
    "viewer": "viewer",
    "analyst": "viewer",
    "analyst / viewer": "viewer",
    "analyst/viewer": "viewer",
    "member": "viewer",
}

ROLE_LABELS: dict[str, str] = {
    "owner": "Owner",
    "admin": "Admin",
    "sales_manager": "Manager",
    "sales_rep": "Sales Rep",
    "viewer": "Analyst / Viewer",
}

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "owner": PERMISSION_CATALOG,
    "admin": PERMISSION_CATALOG,
    "sales_manager": frozenset(
        {
            "leads.view",
            "leads.edit",
            "leads.reassign",
            "analytics.view",
            "analytics.compare",
            "manual_review.view",
            "manual_review.manage",
            "routing.view",
            "members.view",
            "agents.view",
            "agents.execute",
            "dialer.call",
            "calls.metadata.view",
            "calls.transcript.view",
            "calls.recording.play",
            "calls.analysis.view",
            "mailbox.view_own",
            "mailbox.connect_own",
            "mailbox.disconnect_own",
            "mailbox.sync_own",
            "mailbox.send_own",
            "comments.view",
            "comments.add",
            "comments.edit_own",
            "activity.view",
            "fields.contact.view",
            "fields.contact.edit",
            "fields.financial.view",
            "fields.financial.edit",
            "fields.communications.view",
            "fields.internal.view",
        }
    ),
    "sales_rep": frozenset(
        {
            "leads.view",
            "members.view",
            "leads.edit",
            "analytics.view",
            "manual_review.view",
            "routing.view",
            "agents.view",
            "agents.execute",
            "dialer.call",
            "calls.metadata.view",
            "calls.transcript.view",
            "calls.recording.play",
            "calls.analysis.view",
            "mailbox.view_own",
            "mailbox.connect_own",
            "mailbox.disconnect_own",
            "mailbox.sync_own",
            "mailbox.send_own",
            "comments.view",
            "comments.add",
            "comments.edit_own",
            "activity.view",
            "fields.contact.view",
            "fields.contact.edit",
            "fields.financial.view",
            "fields.financial.edit",
            "fields.communications.view",
        }
    ),
    "viewer": frozenset(
        {
            "leads.view",
            "analytics.view",
            "manual_review.view",
            "routing.view",
            "integrations.view",
            "icp.view",
            "agents.view",
            "members.view",
            "comments.view",
            "activity.view",
        }
    ),
}


ROLE_ACCESS_SCOPES: dict[str, dict[str, AccessScope]] = {
    "owner": {"leads.view": AccessScope.ALL, "leads.edit": AccessScope.ALL,
              "analytics.view": AccessScope.ALL},
    "admin": {"leads.view": AccessScope.ALL, "leads.edit": AccessScope.ALL,
              "analytics.view": AccessScope.ALL},
    "sales_manager": {"leads.view": AccessScope.TEAM, "leads.edit": AccessScope.TEAM,
                      "analytics.view": AccessScope.TEAM},
    "sales_rep": {"leads.view": AccessScope.OWN, "leads.edit": AccessScope.OWN,
                  "analytics.view": AccessScope.OWN},
    "viewer": {"leads.view": AccessScope.CUSTOM, "leads.edit": AccessScope.NONE,
               "analytics.view": AccessScope.CUSTOM},
}


def normalize_role(role: str) -> str:
    normalized = role.strip().lower().replace("-", " ")
    canonical = ROLE_ALIASES.get(normalized)
    if canonical is None:
        raise ValueError(f"unsupported workspace role: {role}")
    return canonical


def role_permissions(role: str) -> frozenset[str]:
    canonical = normalize_role(role)
    return ROLE_PERMISSIONS[canonical]


def normalize_overrides(overrides: dict[str, Any] | None) -> dict[str, list[str]]:
    value = overrides or {}
    grants_raw = value.get("grants", [])
    denies_raw = value.get("denies", [])
    if not isinstance(grants_raw, list) or not isinstance(denies_raw, list):
        raise ValueError("permission overrides must contain grants and denies lists")
    grants = sorted({str(permission) for permission in grants_raw})
    denies = sorted({str(permission) for permission in denies_raw})
    unknown = (set(grants) | set(denies)) - PERMISSION_CATALOG
    if unknown:
        raise ValueError(f"unknown permissions: {', '.join(sorted(unknown))}")
    return {"grants": grants, "denies": denies}


def effective_permissions(role: str, overrides: dict[str, Any] | None = None) -> frozenset[str]:
    normalized = normalize_overrides(overrides)
    permissions = set(role_permissions(role))
    permissions.update(normalized["grants"])
    permissions.difference_update(normalized["denies"])
    return frozenset(permissions)


def effective_override_metadata(overrides: dict[str, Any] | None = None) -> dict[str, list[str]]:
    return normalize_overrides(overrides)


def normalize_access_policy(policy: dict[str, Any] | None) -> dict[str, dict[str, str]]:
    value = policy or {}
    scopes_raw = value.get("scopes", {})
    if not isinstance(scopes_raw, dict):
        raise ValueError("access policy scopes must be an object")
    scopes: dict[str, str] = {}
    for permission, raw_scope in scopes_raw.items():
        permission_name = str(permission)
        if permission_name not in PERMISSION_CATALOG:
            raise ValueError(f"unknown scoped permission: {permission_name}")
        try:
            scopes[permission_name] = AccessScope(str(raw_scope).lower()).value
        except ValueError as error:
            raise ValueError(f"unsupported access scope for {permission_name}") from error
    return {"scopes": dict(sorted(scopes.items()))}


def effective_access_scope(
    role: str, permission: str, policy: dict[str, Any] | None = None,
) -> AccessScope:
    canonical = normalize_role(role)
    normalized = normalize_access_policy(policy)
    override = normalized["scopes"].get(permission)
    if override is not None:
        return AccessScope(override)
    return ROLE_ACCESS_SCOPES.get(canonical, {}).get(permission, AccessScope.NONE)
