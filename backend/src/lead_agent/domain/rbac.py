from __future__ import annotations

from enum import StrEnum
from typing import Any


class AccessScope(StrEnum):
    NONE = "none"
    OWN = "own"
    TEAM = "team"
    ALL = "all"
    CUSTOM = "custom"


PROVIDER_REGISTRY: dict[str, dict[str, str]] = {
    "salesforce": {"label": "Salesforce", "category": "crm"},
    "hubspot": {"label": "HubSpot", "category": "crm"},
    "gohighlevel": {"label": "GoHighLevel", "category": "crm"},
    "gmail": {"label": "Gmail", "category": "email_calendar"},
    "outlook": {"label": "Outlook", "category": "email_calendar"},
    "google_calendar": {"label": "Google Calendar", "category": "email_calendar"},
    "calendly": {"label": "Calendly", "category": "email_calendar"},
    "cloudtalk": {"label": "CloudTalk", "category": "calling"},
    "twilio": {"label": "Twilio", "category": "calling"},
    "aircall": {"label": "Aircall", "category": "calling"},
    "ringcentral": {"label": "RingCentral", "category": "calling"},
    "vapi": {"label": "Vapi", "category": "calling"},
    "retell": {"label": "Retell", "category": "calling"},
    "openai": {"label": "OpenAI", "category": "ai_automation"},
    "gemini": {"label": "Gemini", "category": "ai_automation"},
    "claude": {"label": "Claude", "category": "ai_automation"},
    "deepseek": {"label": "DeepSeek", "category": "ai_automation"},
    "xai": {"label": "xAI", "category": "ai_automation"},
    "slack": {"label": "Slack", "category": "channels_sources"},
    "linkedin": {"label": "LinkedIn", "category": "channels_sources"},
    "instagram": {"label": "Instagram", "category": "channels_sources"},
    "messenger": {"label": "Messenger", "category": "channels_sources"},
    "whatsapp_business": {
        "label": "WhatsApp Business",
        "category": "channels_sources",
    },
    "clutch": {"label": "Clutch", "category": "channels_sources"},
}

PROVIDER_KEYS: tuple[str, ...] = tuple(PROVIDER_REGISTRY)
PROVIDER_ACTIONS: tuple[str, ...] = (
    "view", "view_status", "connect_own", "use", "sync", "send", "disconnect_own",
    "manage_own", "manage_shared", "configure", "manage_credentials",
)
PROVIDER_PERMISSIONS: frozenset[str] = frozenset(
    f"integrations.{provider}.{action}"
    for provider in PROVIDER_KEYS
    for action in PROVIDER_ACTIONS
)

PERMISSION_LABELS: dict[str, str] = {
    "leads.view": "View leads",
    "leads.edit": "Edit leads",
    "leads.export": "Export leads",
    "leads.reassign": "Reassign leads",
    "leads.delete_restore": "Delete and restore leads",
    "leads.share": "Share leads",
    "leads.access.manage": "Manage lead access",
    "comments.view": "View team notes and comments",
    "comments.add": "Add notes and comments",
    "comments.edit_own": "Edit own notes",
    "comments.edit_shared": "Edit shared notes",
    "comments.delete": "Delete notes",
    "comments.view_private": "View private notes",
    "activity.view": "View lead activity history",
    "analytics.view": "View analytics",
    "analytics.compare": "Compare teams and reps",
    "analytics.sensitive": "View sensitive metrics",
    "manual_review.view": "View manual review",
    "manual_review.manage": "Manage manual review",
    "routing.view": "View routing",
    "routing.manage": "Manage routing",
    "integrations.view": "View integrations",
    "integrations.manage": "Manage integrations",
    "integrations.connect": "Connect integrations",
    "integrations.disconnect": "Disconnect integrations",
    "icp.view": "View ICP",
    "icp.manage": "Manage ICP",
    "members.view": "View members",
    "members.invite": "Invite members",
    "members.manage": "Manage members",
    "agents.view": "View agent activity",
    "agents.execute": "Execute permitted agent actions",
    "agents.configure": "Configure agents",
    "agents.logs.view": "View agent logs",
    "agents.external_actions": "Run external actions",
    "agents.approve_high_impact": "Approve high-impact actions",
    "dialer.call": "Place calls",
    "calls.metadata.view": "View call metadata",
    "calls.transcript.view": "View transcripts",
    "calls.recording.play": "Play recordings",
    "calls.recording.download": "Download recordings",
    "calls.analysis.view": "View call analysis",
    "mailbox.view_own": "View own mailbox",
    "mailbox.connect_own": "Connect own mailbox",
    "mailbox.disconnect_own": "Disconnect own mailbox",
    "mailbox.sync_own": "Sync own mailbox",
    "mailbox.send_own": "Send from own mailbox",
    "mailbox.delegate": "Delegate shared mailbox",
    "privacy.manage": "Manage privacy policies",
    "audit.view": "View security audit",
    "fields.contact.view": "View contact fields",
    "fields.contact.edit": "Edit contact fields",
    "fields.financial.view": "View financial fields",
    "fields.financial.edit": "Edit financial fields",
    "fields.communications.view": "View communication content",
    "fields.communications.edit": "Edit communication content",
    "fields.internal.view": "View internal fields",
    "fields.internal.edit": "Edit internal fields",
    "fields.restricted.view": "View restricted fields",
    "fields.restricted.edit": "Edit restricted fields",
    "crm.sync": "Run permitted CRM synchronization",
    "workspace.manage": "Manage workspace",
}

PROVIDER_ACTION_LABELS: dict[str, str] = {
    "view": "View connection",
    "view_status": "View connection status",
    "connect_own": "Connect own account",
    "use": "Use integration",
    "sync": "Sync records",
    "send": "Send through integration",
    "disconnect_own": "Disconnect own account",
    "manage_own": "Manage own account",
    "manage_shared": "Manage shared accounts",
    "configure": "Configure settings",
    "manage_credentials": "Manage credentials",
}

ACCESS_CATEGORY_REGISTRY: tuple[dict[str, Any], ...] = (
    {
        "id": "leads",
        "label": "Leads",
        "description": "Lead records, ownership, sharing and exports.",
        "feature_permissions": (
            "leads.view", "leads.edit", "leads.export", "leads.reassign",
            "leads.delete_restore", "leads.share", "leads.access.manage",
        ),
        "providers": (),
        "sensitive": True,
    },
    {
        "id": "collaboration",
        "label": "Collaboration",
        "description": "Team notes, private comments and lead activity.",
        "feature_permissions": (
            "comments.view", "comments.add", "comments.edit_own",
            "comments.edit_shared", "comments.delete", "comments.view_private",
            "activity.view",
        ),
        "providers": (),
        "sensitive": True,
    },
    {
        "id": "analytics",
        "label": "Analytics",
        "description": "Workspace reporting, comparisons and sensitive metrics.",
        "feature_permissions": (
            "analytics.view", "analytics.compare", "analytics.sensitive",
        ),
        "providers": (),
        "sensitive": True,
    },
    {
        "id": "review_routing",
        "label": "Review & routing",
        "description": "Manual review queues and lead routing controls.",
        "feature_permissions": (
            "manual_review.view", "manual_review.manage", "routing.view",
            "routing.manage",
        ),
        "providers": (),
        "sensitive": True,
    },
    {
        "id": "crm",
        "label": "CRM & records",
        "description": "CRM synchronization and provider-specific access.",
        "feature_permissions": ("crm.sync",),
        "providers": ("salesforce", "hubspot", "gohighlevel"),
        "sensitive": True,
    },
    {
        "id": "email_calendar",
        "label": "Email & calendar",
        "description": "Mailbox features, email delivery and scheduling providers.",
        "feature_permissions": (
            "mailbox.view_own", "mailbox.connect_own", "mailbox.disconnect_own",
            "mailbox.sync_own", "mailbox.send_own", "mailbox.delegate",
        ),
        "providers": ("gmail", "outlook", "google_calendar", "calendly"),
        "sensitive": True,
    },
    {
        "id": "calling",
        "label": "Calling",
        "description": "Dialing, recordings, transcripts, analysis and phone providers.",
        "feature_permissions": (
            "dialer.call", "calls.metadata.view", "calls.transcript.view",
            "calls.recording.play", "calls.recording.download",
            "calls.analysis.view",
        ),
        "providers": (
            "cloudtalk", "twilio", "aircall", "ringcentral", "vapi", "retell",
        ),
        "sensitive": True,
    },
    {
        "id": "ai_automation",
        "label": "AI & automation",
        "description": "Agent actions, approvals, logs and AI providers.",
        "feature_permissions": (
            "agents.view", "agents.execute", "agents.configure", "agents.logs.view",
            "agents.external_actions", "agents.approve_high_impact",
        ),
        "providers": ("openai", "gemini", "claude", "deepseek", "xai"),
        "sensitive": True,
    },
    {
        "id": "channels_sources",
        "label": "Channels & sources",
        "description": "Messaging, notifications and external lead sources.",
        "feature_permissions": (),
        "providers": (
            "slack", "linkedin", "instagram", "messenger",
            "whatsapp_business", "clutch",
        ),
        "sensitive": False,
    },
    {
        "id": "data_icp",
        "label": "Data & ICP",
        "description": "ICP configuration and protected contact, financial and internal fields.",
        "feature_permissions": (
            "icp.view", "icp.manage", "fields.contact.view", "fields.contact.edit",
            "fields.financial.view", "fields.financial.edit",
            "fields.communications.view", "fields.communications.edit",
            "fields.internal.view", "fields.internal.edit",
            "fields.restricted.view", "fields.restricted.edit",
        ),
        "providers": (),
        "sensitive": True,
    },
    {
        "id": "team_workspace",
        "label": "Team & workspace",
        "description": "Members, workspace administration, privacy and integration setup.",
        "feature_permissions": (
            "members.view", "members.invite", "members.manage",
            "workspace.manage", "privacy.manage", "audit.view",
            "integrations.view", "integrations.manage", "integrations.connect",
            "integrations.disconnect",
        ),
        "providers": (),
        "sensitive": True,
    },
)

PROVIDER_CATEGORY_BY_KEY: dict[str, str] = {
    provider: details["category"] for provider, details in PROVIDER_REGISTRY.items()
}

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
        "communication.view",
        "communication.create",
        "communication.manage",
        "communication.search",
        "communication.attach",
        "communication.moderate",
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

# Provider defaults are intentionally an explicit compatibility snapshot. Adding a
# provider to PROVIDER_REGISTRY makes its permission IDs valid, but does not grant
# them to any role until that provider is deliberately added here or through a
# member exception. This keeps new providers deny-by-default.
DEFAULT_PROVIDER_KEYS: tuple[str, ...] = (
    "salesforce", "hubspot", "gohighlevel", "gmail", "outlook",
    "google_calendar", "calendly", "cloudtalk", "twilio", "aircall",
    "ringcentral", "vapi", "retell", "openai", "gemini", "claude",
    "deepseek", "xai", "slack", "linkedin", "instagram", "messenger",
    "whatsapp_business", "clutch",
)
DEFAULT_PROVIDER_PERMISSIONS: frozenset[str] = frozenset(
    f"integrations.{provider}.{action}"
    for provider in DEFAULT_PROVIDER_KEYS
    for action in PROVIDER_ACTIONS
)

_MAILBOX_COMPAT_PERMISSIONS: frozenset[str] = frozenset(
    f"integrations.{provider}.{action}"
    for provider in ("gmail", "outlook")
    for action in (
        "view", "view_status", "connect_own", "use", "sync", "send",
        "disconnect_own", "manage_own",
    )
)
_CALLING_COMPAT_PERMISSIONS: frozenset[str] = frozenset(
    f"integrations.{provider}.{action}"
    for provider in ("cloudtalk", "twilio", "aircall", "ringcentral", "vapi", "retell")
    for action in ("view", "use")
)
_AI_COMPAT_PERMISSIONS: frozenset[str] = frozenset(
    f"integrations.{provider}.{action}"
    for provider in ("openai", "gemini", "claude", "deepseek", "xai")
    for action in ("view", "view_status", "use")
)
_LEGACY_PROVIDER_EXPANSIONS: dict[str, frozenset[str]] = {
    "mailbox.view_own": frozenset(
        permission
        for permission in _MAILBOX_COMPAT_PERMISSIONS
        if permission.endswith((".view", ".view_status"))
    ),
    "mailbox.connect_own": frozenset(
        permission
        for permission in _MAILBOX_COMPAT_PERMISSIONS
        if permission.endswith((".connect_own", ".manage_own"))
    ),
    "mailbox.disconnect_own": frozenset(
        permission
        for permission in _MAILBOX_COMPAT_PERMISSIONS
        if permission.endswith(".disconnect_own")
    ),
    "mailbox.sync_own": frozenset(
        permission
        for permission in _MAILBOX_COMPAT_PERMISSIONS
        if permission.endswith((".use", ".sync"))
    ),
    "mailbox.send_own": frozenset(
        permission for permission in _MAILBOX_COMPAT_PERMISSIONS if permission.endswith(".send")
    ),
    "dialer.call": _CALLING_COMPAT_PERMISSIONS,
    "agents.view": _AI_COMPAT_PERMISSIONS,
}


def _compat_provider_permissions(features: set[str] | frozenset[str]) -> frozenset[str]:
    return frozenset(
        permission
        for feature, provider_permissions in _LEGACY_PROVIDER_EXPANSIONS.items()
        if feature in features
        for permission in provider_permissions
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
    "owner": (PERMISSION_CATALOG - PROVIDER_PERMISSIONS) | DEFAULT_PROVIDER_PERMISSIONS,
    "admin": (PERMISSION_CATALOG - PROVIDER_PERMISSIONS) | DEFAULT_PROVIDER_PERMISSIONS,
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
            "communication.view",
            "communication.create",
            "communication.search",
            "communication.attach",
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
            "communication.view",
            "communication.create",
            "communication.search",
            "communication.attach",
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
            "communication.view",
            "communication.search",
            "communication.create",
            "communication.attach",
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

for _role_name in ("sales_manager", "sales_rep", "viewer"):
    ROLE_PERMISSIONS[_role_name] = (
        ROLE_PERMISSIONS[_role_name]
        | _compat_provider_permissions(ROLE_PERMISSIONS[_role_name])
    )


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


def can_sub_assign_leads(role: str) -> bool:
    return normalize_role(role) in {"owner", "admin", "sales_manager"}


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
    grants = set(normalized["grants"])
    denies = set(normalized["denies"])
    permissions = set(role_permissions(role))
    permissions.update(grants)
    permissions.difference_update(denies)
    for feature, provider_permissions in _LEGACY_PROVIDER_EXPANSIONS.items():
        if feature in permissions:
            permissions.update(provider_permissions - denies)
        elif feature in denies:
            # A historical broad deny must continue to remove access that used
            # to be supplied by the broad feature fallback. An explicit exact
            # provider grant still wins, matching the old behavior.
            permissions.difference_update(provider_permissions - grants)
    return frozenset(permissions)


def permission_registry() -> dict[str, Any]:
    """Return the UI registry from the same source used by authorization."""
    categories: list[dict[str, Any]] = []
    for category in ACCESS_CATEGORY_REGISTRY:
        providers = [
            {
                "id": provider,
                "label": PROVIDER_REGISTRY[provider]["label"],
            }
            for provider in category["providers"]
        ]
        categories.append(
            {
                "id": category["id"],
                "label": category["label"],
                "description": category["description"],
                "sensitive": category["sensitive"],
                "feature_permissions": [
                    {"id": permission, "label": PERMISSION_LABELS[permission]}
                    for permission in category["feature_permissions"]
                ],
                "providers": providers,
                "provider_actions": [
                    {"id": action, "label": PROVIDER_ACTION_LABELS[action]}
                    for action in PROVIDER_ACTIONS
                ] if providers else [],
            }
        )
    return {
        "categories": categories,
        "roles": [
            {
                "id": role,
                "label": label,
                "default_permissions": sorted(role_permissions(role)),
            }
            for role, label in ROLE_LABELS.items()
        ],
    }


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
