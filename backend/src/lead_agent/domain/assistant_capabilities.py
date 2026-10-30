"""Server-owned workspace action contracts supplied to every AI provider."""
from collections.abc import Collection

# These are capability descriptions, not authorization. The actual mutation APIs
# and the review UI still enforce their own permissions and confirmation gates.
WORKSPACE_ACTION_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "open_module": (),
    "open_lead": ("leads.view",),
    "open_deal_desk": ("leads.view",),
    "create_lead": ("leads.edit",),
    "update_lead_profile": ("leads.view", "leads.edit"),
    "add_lead_note": ("leads.view", "leads.edit"),
    "record_lead_progress": ("leads.view", "leads.edit"),
    "reevaluate_lead": ("leads.view", "leads.edit"),
    "complete_lead_manual_review": ("leads.view", "manual_review.manage"),
    "send_email": ("leads.edit",),
    "set_automation_active": ("workspace.manage",),
    "create_icp_draft": ("icp.manage",),
    "create_automation_draft": ("agents.view", "workspace.manage"),
    "enroll_lead_in_automation": ("leads.view", "leads.edit", "agents.view"),
    "update_routing_config": ("routing.manage",),
    "set_lead_confirmation": ("integrations.manage",),
    "disconnect_integration": ("integrations.disconnect",),
    "invite_member": ("members.invite",),
    "update_member_role": ("members.manage",),
    "update_member_permissions": ("members.manage",),
    "suspend_member": ("members.manage",),
    "reactivate_member": ("members.manage",),
}

# Every page the assistant may open, with the permissions that unlock it. Kept
# in step with the frontend's view permissions so the agent never offers a page
# the person cannot see.
WORKSPACE_AREA_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "dashboard": ("analytics.view",),
    "leads": ("leads.view",),
    "pipeline": ("analytics.view",),
    "revenue": ("analytics.view",),
    "review": ("manual_review.view",),
    "email": ("integrations.view",),
    "calendar": ("leads.view",),
    "dialer": ("dialer.call",),
    "contacts": ("leads.view",),
    "accounts": ("leads.view",),
    "performance": ("analytics.view",),
    "analytics": ("analytics.view",),
    "agents": ("agents.view",),
    "intelligence": ("agents.view",),
    "automations": ("agents.view",),
    "integrations": ("integrations.view",),
    "team": ("members.view",),
    "settings": ("routing.view", "integrations.view", "workspace.manage"),
    "icp": ("icp.view",),
    "policies": ("routing.view",),
    "compliance": ("workspace.manage",),
}

_REQUIRED_FIELDS = {
    "open_module": ["area"],
    "open_lead": ["target_id"],
    "open_deal_desk": ["target_id: verified workspace lead"],
    "create_lead": ["payload_json: at least one known lead detail; no target_id"],
    "update_lead_profile": ["target_id", "payload_json: supported profile fields"],
    "add_lead_note": ["target_id", "payload_json: note"],
    "record_lead_progress": ["target_id", "payload_json: stage and note"],
    "reevaluate_lead": ["target_id"],
    "complete_lead_manual_review": [
        "target_id: lead with an active manual-review hold"
    ],
    "send_email": [
        "email_subject", "email_body", "recipient reviewed in UI", "verified Gmail sender"
    ],
    "set_automation_active": ["target_id", "enabled"],
    "create_icp_draft": ["payload_json"],
    "create_automation_draft": [
        "payload_json: name, kind (sequence|playbook), steps[] of "
        "{type: wait|draft|research|coach|note, wait_hours for wait, text}"
    ],
    "enroll_lead_in_automation": [
        "target_id: verified workspace lead",
        "payload_json: definition_id of an active automation",
    ],
    "update_routing_config": ["payload_json"],
    "set_lead_confirmation": ["enabled"],
    "disconnect_integration": ["target_id"],
    "invite_member": ["email", "role"],
    "update_member_role": ["target_id", "role"],
    "update_member_permissions": ["target_id", "payload_json"],
    "suspend_member": ["target_id"],
    "reactivate_member": ["target_id"],
}


def workspace_action_capabilities(permissions: Collection[str]) -> list[dict[str, object]]:
    area_permissions = WORKSPACE_AREA_PERMISSIONS
    areas = [
        area for area, required in area_permissions.items()
        if any(permission in permissions for permission in required)
    ]
    return [
        {
            "kind": kind,
            "required_fields": _REQUIRED_FIELDS[kind],
            "requires_review_and_approval": kind not in {
                "open_module", "open_lead", "open_deal_desk",
            },
            **({"allowed_areas": areas} if kind == "open_module" else {}),
        }
        for kind, required in WORKSPACE_ACTION_PERMISSIONS.items()
        if all(permission in permissions for permission in required)
        and (kind != "open_module" or areas)
    ]
