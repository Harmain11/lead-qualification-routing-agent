from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field

CrmSyncStatus = Literal["pending", "syncing", "synced", "failed", "retryable"]
CrmOperation = Literal["create", "update", "upsert"]
CrmErrorCategory = Literal[
    "authentication",
    "configuration",
    "rate_limit",
    "transient",
    "mapping",
    "validation",
    "unknown",
]


class CrmEntityReferences(BaseModel):
    contact_id: str | None = None
    company_id: str | None = None
    opportunity_id: str | None = None
    contact_id_is_direct: bool = Field(default=False, exclude=True)


class CanonicalCrmPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    payload_version: str = "1.0"
    internal_lead_id: UUID
    external_source_id: str | None = None
    full_name: str
    first_name: str
    last_name: str
    email: str
    company: str
    company_key: str
    title: str | None = None
    phone: str | None = None
    source_channel: str
    source_detail: str | None = None
    requested_service: str
    summary: str
    qualification_score: int = Field(ge=0, le=100)
    qualification_band: str
    icp_score: int | None = Field(default=None, ge=0, le=40)
    icp_version_id: UUID | None = None
    icp_source: str = "not_configured"
    icp_profile_version: str = "legacy"
    icp_configuration_hash: str = "legacy"
    intent_urgency_score: int = Field(ge=0, le=35)
    engagement_score: int = Field(ge=0, le=25)
    rationale: list[str]
    recommended_action: str
    action_plan: dict[str, object]
    assigned_queue: str
    assigned_rep_id: str | None = None
    assigned_rep_name: str | None = None
    urgency_timeline: str
    lifecycle_stage: str


class CrmSyncResult(BaseModel):
    provider: str
    operation: CrmOperation = "upsert"
    entities: CrmEntityReferences


class CrmProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        category: CrmErrorCategory,
        retryable: bool,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.category = category
        self.retryable = retryable


class CrmProvider(Protocol):
    provider_name: str

    def sync(
        self,
        payload: CanonicalCrmPayload,
        existing: CrmEntityReferences | None = None,
    ) -> CrmSyncResult: ...


class SalesforceCrmConfig(BaseModel):
    lead_object: str = "Lead"
    lead_external_id_field: str = "Lead_Agent_ID__c"
    lead_fields: dict[str, str] = Field(default_factory=dict)
    owner_mapping: dict[str, str] = Field(default_factory=dict)
    sync_opportunity: bool = False
    opportunity_object: str = "Opportunity"
    opportunity_external_id_field: str = "Lead_Agent_ID__c"
    opportunity_fields: dict[str, str] = Field(default_factory=dict)
    opportunity_stage_by_band: dict[str, str] = Field(default_factory=dict)


class HubSpotCrmConfig(BaseModel):
    contact_object: str = "contacts"
    contact_id_property: str = "email"
    contact_fields: dict[str, str] = Field(default_factory=dict)
    owner_mapping: dict[str, str] = Field(default_factory=dict)
    sync_company: bool = False
    company_object: str = "companies"
    company_id_property: str = "lead_agent_company_key"
    company_fields: dict[str, str] = Field(default_factory=dict)
    sync_deal: bool = False
    deal_object: str = "deals"
    deal_id_property: str = "lead_agent_lead_id"
    deal_pipeline: str = "default"
    deal_fields: dict[str, str] = Field(default_factory=dict)
    deal_stage_by_band: dict[str, str] = Field(default_factory=dict)


class CrmConfig(BaseModel):
    schema_version: int = 1
    payload_version: str = "1.0"
    salesforce: SalesforceCrmConfig = Field(default_factory=SalesforceCrmConfig)
    hubspot: HubSpotCrmConfig = Field(default_factory=HubSpotCrmConfig)


def load_crm_config(path: Path) -> CrmConfig:
    with path.open("r", encoding="utf-8") as stream:
        loaded: Any = yaml.safe_load(stream) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"CRM configuration root must be a mapping: {path}")
    return CrmConfig.model_validate(loaded)
