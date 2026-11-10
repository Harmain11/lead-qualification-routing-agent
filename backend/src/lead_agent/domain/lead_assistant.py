from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ProgressStage = Literal[
    "contacted",
    "responded",
    "meeting_scheduled",
    "proposal_sent",
    "converted",
    "lost",
]


class LeadProfileChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=64)
    job_title: str | None = Field(default=None, max_length=255)
    company_name: str | None = Field(default=None, max_length=255)
    industry: str | None = Field(default=None, max_length=255)
    employee_count: int | None = Field(default=None, ge=0)
    annual_revenue: float | None = Field(default=None, ge=0)
    geography: str | None = Field(default=None, max_length=255)
    budget: float | None = Field(default=None, ge=0)
    product_or_service: str | None = Field(default=None, max_length=255)


class LeadAssistantAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "none",
        "update_profile",
        "add_note",
        "record_progress",
        "draft_email",
        "send_follow_up",
        "refresh_email",
        "sync_crm",
        "delete_lead",
        "reevaluate_lead",
        "complete_manual_review",
        "open_email",
        "open_meetings",
        "open_dialer",
        "open_closing",
        "open_history",
    ] = "none"
    profile_updates: LeadProfileChanges = Field(default_factory=LeadProfileChanges)
    clear_fields: list[
        Literal[
            "phone",
            "job_title",
            "industry",
            "employee_count",
            "annual_revenue",
            "geography",
            "budget",
        ]
    ] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=5000)
    progress_stage: ProgressStage | None = None
    progress_note: str | None = Field(default=None, max_length=2000)
    conversion_type: Literal["project", "sale"] | None = None
    closed_revenue: float | None = Field(default=None, ge=0)
    credited_user_id: str | None = Field(default=None, max_length=64)
    email_subject: str | None = Field(default=None, max_length=998)
    email_body: str | None = Field(default=None, max_length=20000)
    crm_provider: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def route_note_only_changes(self) -> LeadAssistantAction:
        # A note is narrative context, not a profile field. Some provider
        # responses use update_profile with only note populated.
        if (
            self.kind == "update_profile"
            and self.note and self.note.strip()
            and not self.clear_fields
            and not any(value is not None for value in self.profile_updates.model_dump().values())
        ):
            self.kind = "add_note"
        return self


class LeadAssistantReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply: str = Field(min_length=1, max_length=3000)
    action: LeadAssistantAction = Field(default_factory=LeadAssistantAction)


class WorkspaceAssistantAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "none",
        "open_module",
        "open_lead",
        "open_deal_desk",
        "create_lead",
        "set_automation_active",
        "create_icp_draft",
        "create_automation_draft",
        "enroll_lead_in_automation",
        "update_routing_config",
        "set_lead_confirmation",
        "disconnect_integration",
        "invite_member",
        "update_member_role",
        "update_member_permissions",
        "suspend_member",
        "reactivate_member",
        "send_email",
        "update_lead_profile",
        "add_lead_note",
        "record_lead_progress",
        "reevaluate_lead",
        "complete_lead_manual_review",
    ] = "none"
    area: Literal[
        "dashboard",
        "leads",
        "pipeline",
        "revenue",
        "review",
        "email",
        "calendar",
        "dialer",
        "contacts",
        "accounts",
        "performance",
        "analytics",
        "agents",
        "intelligence",
        "automations",
        "integrations",
        "team",
        "settings",
        "icp",
        "policies",
        "compliance",
    ] | None = None
    title: str = Field(default="", max_length=120)
    summary: str = Field(default="", max_length=800)
    target_id: str | None = Field(default=None, max_length=64)
    target_label: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=320)
    email_recipient: str | None = Field(default=None, max_length=1024)
    email_cc: str | None = Field(default=None, max_length=1024)
    email_bcc: str | None = Field(default=None, max_length=1024)
    email_subject: str | None = Field(default=None, max_length=180)
    email_body: str | None = Field(default=None, max_length=8000)
    role: str | None = Field(default=None, max_length=64)
    enabled: bool | None = None
    payload_json: str | None = Field(default=None, max_length=12000)


class WorkspaceAssistantReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply: str = Field(min_length=1, max_length=3000)
    action: WorkspaceAssistantAction = Field(default_factory=WorkspaceAssistantAction)

