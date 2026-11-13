from __future__ import annotations

from typing import Any

PERMISSION_CATALOG: frozenset[str] = frozenset(
    {
        "leads.view",
        "leads.edit",
        "leads.export",
        "analytics.view",
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
        "crm.sync",
        "workspace.manage",
        "communication.view",
        "communication.create",
        "communication.manage",
        "communication.search",
        "communication.attach",
        "communication.moderate",
    }
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
    "sales_manager": "Sales Manager",
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
            "leads.export",
            "analytics.view",
            "manual_review.view",
            "manual_review.manage",
            "routing.view",
            "routing.manage",
            "members.view",
            "agents.view",
            "agents.execute",
            "crm.sync",
            "communication.view",
            "communication.create",
            "communication.search",
            "communication.attach",
        }
    ),
    "sales_rep": frozenset(
        {
            "leads.view",
            "leads.edit",
            "analytics.view",
            "manual_review.view",
            "routing.view",
            "agents.view",
            "agents.execute",
            "crm.sync",
            "communication.view",
            "communication.create",
            "communication.search",
            "communication.attach",
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
            "communication.view",
            "communication.search",
        }
    ),
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
