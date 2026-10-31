"""Validated workspace policies and a bounded, acyclic playbook language."""

import re
from datetime import datetime
from typing import Any, Literal, Self
from urllib.parse import urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def email_key(value: str) -> str:
    value = value.strip().casefold()
    if len(value) > 320 or value.count("@") != 1 or any(c.isspace() for c in value):
        raise ValueError("A valid email address is required")
    local, domain = value.split("@")
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise ValueError("A valid email address is required")
    return value


def safe_website(value: str) -> str:
    parts = urlsplit(value)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.port not in (None, 443)
    ):
        raise ValueError("Use a public HTTPS website without credentials or a custom port")
    return value


class WorkspaceBusinessProfile(StrictModel):
    """Optional identity context for the organization that owns this CRM workspace."""

    business_name: str = Field(default="", max_length=255)
    alternate_names: list[str] = Field(default_factory=list, max_length=25)
    website: str = Field(default="", max_length=500)
    email_domains: list[str] = Field(default_factory=list, max_length=25)
    description: str = Field(default="", max_length=2000)
    products_services: str = Field(default="", max_length=2000)
    lead_distinction_notes: str = Field(default="", max_length=1500)
    email_signature: str = Field(default="", max_length=1200)

    @field_validator("alternate_names", "email_domains")
    @classmethod
    def clean_identity_values(cls, values: list[str], info: Any) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = raw.strip()
            if info.field_name == "email_domains":
                value = value.casefold().removeprefix("@")
                if value and not re.fullmatch(
                    r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}",
                    value,
                ):
                    raise ValueError("Enter email domains like example.com, without an @ sign")
            key = value.casefold()
            if value and key not in seen:
                if len(value) > 255:
                    raise ValueError("Business names and domains must be 255 characters or fewer")
                normalized.append(value)
                seen.add(key)
        return normalized

    @field_validator("website")
    @classmethod
    def website_is_https(cls, value: str) -> str:
        return safe_website(value) if value else value

    @property
    def has_context(self) -> bool:
        return any(self.model_dump().values())


class Territory(StrictModel):
    id: UUID
    name: str = Field(min_length=1, max_length=100)
    member_ids: list[UUID] = Field(default_factory=list, max_length=200)
    regions: list[str] = Field(default_factory=list, max_length=50)
    industries: list[str] = Field(default_factory=list, max_length=50)
    products: list[str] = Field(default_factory=list, max_length=50)
    sla_hours: int = Field(default=24, ge=1, le=720)


class RepCapacity(StrictModel):
    user_id: UUID
    max_open_leads: int = Field(default=30, ge=0, le=10000)
    available: bool = True
    available_until: datetime | None = None


class ChannelCost(StrictModel):
    source: str = Field(min_length=1, max_length=64)
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    cost_usd: float = Field(ge=0, le=1e12, allow_inf_nan=False)


class Battlecard(StrictModel):
    competitor: str = Field(min_length=2, max_length=100)
    response: str = Field(min_length=1, max_length=2000)
    evidence_url: str | None = Field(default=None, max_length=500)

    @field_validator("evidence_url")
    @classmethod
    def https_evidence(cls, value: str | None) -> str | None:
        return safe_website(value) if value else None


def default_approval_tiers() -> dict[
    Literal["draft", "research", "coach", "note"], Literal["automatic", "approve", "full_review"]
]:
    return {"draft": "approve", "research": "approve", "coach": "automatic", "note": "automatic"}


class OperationsPolicy(StrictModel):
    territories: list[Territory] = Field(default_factory=list, max_length=100)
    capacity: list[RepCapacity] = Field(default_factory=list, max_length=200)
    channel_costs: list[ChannelCost] = Field(default_factory=list, max_length=1000)
    battlecards: list[Battlecard] = Field(default_factory=list, max_length=100)
    require_recorded_consent: bool = False
    retention_days: int | None = Field(default=None, ge=30, le=3650)
    required_region: Literal["any", "local", "eu", "uk", "gcc", "pk", "us"] = "any"
    watcher_enabled: bool = False
    researcher_enabled: bool = False
    coach_digest_enabled: bool = False
    approval_tiers: dict[
        Literal["draft", "research", "coach", "note"],
        Literal["automatic", "approve", "full_review"],
    ] = Field(default_factory=default_approval_tiers)
    brand_name: str | None = Field(default=None, max_length=100)
    brand_logo_url: str | None = Field(default=None, max_length=500)
    brand_accent: str = Field(default="#1e6b52", pattern=r"^#[0-9a-fA-F]{6}$")
    custom_domain: str | None = Field(
        default=None, max_length=255, pattern=r"^[a-z0-9][a-z0-9.-]+[a-z]$"
    )
    monthly_quota_usd: float | None = Field(default=None, ge=0, le=1e12, allow_inf_nan=False)
    business_profile: WorkspaceBusinessProfile = Field(default_factory=WorkspaceBusinessProfile)

    @field_validator("brand_logo_url")
    @classmethod
    def logo_is_https(cls, value: str | None) -> str | None:
        return safe_website(value) if value else None

    @model_validator(mode="after")
    def unique_policy_keys(self) -> Self:
        for values in (self.territories, self.capacity):
            keys = [str(getattr(value, "id", getattr(value, "user_id", ""))) for value in values]
            if len(keys) != len(set(keys)):
                raise ValueError("Group and capacity IDs must be unique")
        cost_keys = [(value.source, value.month) for value in self.channel_costs]
        if len(cost_keys) != len(set(cost_keys)):
            raise ValueError("Only one source cost per month is allowed")
        if self.approval_tiers.get("draft") == "automatic":
            raise ValueError("Follow-up drafts require human approval")
        return self


CONTACT_PROFILE_FIELDS = frozenset(
    {"linkedin_url", "department", "seniority", "mobile_phone", "preferred_channel", "location", "notes", "tags"}
)
COMPANY_PROFILE_FIELDS = frozenset(
    {
        "description",
        "employees",
        "annual_revenue",
        "headquarters",
        "linkedin_url",
        "phone",
        "founded_year",
        "notes",
        "tags",
    }
)
_LONG_PROFILE_FIELDS = frozenset({"notes", "description"})
_CHANNELS = frozenset({"email", "phone", "sms", "whatsapp"})


def clean_record_profile(value: dict[str, Any], allowed: frozenset[str]) -> dict[str, Any]:
    """Validate the optional extra fields on a contact or company.

    An empty string or empty list means "clear this field"; the route removes it.
    """
    cleaned: dict[str, Any] = {}
    for key, raw in value.items():
        if key not in allowed:
            raise ValueError(f"Unknown profile field: {key}")
        if key == "tags":
            if not isinstance(raw, list) or len(raw) > 20:
                raise ValueError("Tags must be a list of up to 20 labels")
            tags = []
            for tag in raw:
                if not isinstance(tag, str) or len(tag.strip()) > 40:
                    raise ValueError("Each tag must be 40 characters or fewer")
                if tag.strip() and tag.strip() not in tags:
                    tags.append(tag.strip())
            cleaned[key] = tags
            continue
        if raw is None:
            raw = ""
        if not isinstance(raw, str):
            raise ValueError(f"{key} must be text")
        text = raw.strip()
        if len(text) > (2000 if key in _LONG_PROFILE_FIELDS else 300):
            raise ValueError(f"{key} is too long")
        if key.endswith("_url") and text and not text.startswith("https://"):
            raise ValueError("Use a full https:// link")
        if key == "preferred_channel" and text and text not in _CHANNELS:
            raise ValueError("Preferred channel must be email, phone, sms or whatsapp")
        if key == "founded_year" and text and not (text.isdigit() and 1600 <= int(text) <= 2100):
            raise ValueError("Use a four-digit year")
        cleaned[key] = text
    return cleaned


def merge_record_profile(existing: dict[str, Any] | None, update: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing or {})
    for key, value in update.items():
        if value in ("", []):
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


class ContactInput(StrictModel):
    name: str = Field(min_length=1, max_length=255)
    email: str | None = None
    company_id: UUID | None = None
    phone: str | None = Field(default=None, max_length=64)
    job_title: str | None = Field(default=None, max_length=255)
    timezone: str = "UTC"
    profile: dict[str, Any] = Field(default_factory=dict)

    @field_validator("profile")
    @classmethod
    def valid_profile(cls, value: dict[str, Any]) -> dict[str, Any]:
        return clean_record_profile(value, CONTACT_PROFILE_FIELDS)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str | None) -> str | None:
        return email_key(value) if value else None

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError("Use an IANA timezone such as Asia/Karachi") from None
        return value


class CompanyInput(StrictModel):
    name: str = Field(min_length=1, max_length=255)
    domain: str | None = Field(default=None, max_length=255, pattern=r"^[a-z0-9][a-z0-9.-]+[a-z]$")
    website: str | None = Field(default=None, max_length=500)
    website_reviewed: bool = False
    industry: str | None = Field(default=None, max_length=255)
    profile: dict[str, Any] = Field(default_factory=dict)

    @field_validator("profile")
    @classmethod
    def valid_profile(cls, value: dict[str, Any]) -> dict[str, Any]:
        return clean_record_profile(value, COMPANY_PROFILE_FIELDS)

    @model_validator(mode="after")
    def reviewed_website_matches(self) -> Self:
        if self.website:
            safe_website(self.website)
            host = urlsplit(self.website).hostname
            if not host:
                raise ValueError("A valid company website is required")
            hostname = host.removeprefix("www.")
            if self.domain and hostname != self.domain.removeprefix("www."):
                raise ValueError("Website must match the company's domain")
            self.domain = hostname
        elif self.website_reviewed:
            raise ValueError("Review a website before enabling research")
        return self


class ConsentInput(StrictModel):
    email: str
    channel: Literal["all", "email", "sms", "whatsapp", "call", "research"]
    status: Literal["granted", "denied", "withdrawn"]
    basis: Literal["consent", "legitimate_interest", "contract", "objection"]
    purpose: str = Field(min_length=1, max_length=255)
    evidence: str = Field(min_length=1, max_length=4000)

    _email = field_validator("email")(email_key)


class PlaybookNode(StrictModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    x: int = Field(default=60, ge=0, le=4000)
    y: int = Field(default=60, ge=0, le=4000)
    type: Literal["start", "wait", "condition", "draft", "research", "coach", "note", "end"]
    next: str | None = None
    otherwise: str | None = None
    wait_hours: int = Field(default=24, ge=1, le=2160)
    condition: Literal[
        "hot", "warm", "replied", "silent", "won", "lost", "stalled", "competitor"
    ] = "hot"
    text: str = Field(default="", max_length=2000)


class AutomationInput(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["sequence", "playbook"]
    active: bool = False
    nodes: list[PlaybookNode] = Field(min_length=2, max_length=50)
    timezone: str = "UTC"
    preferred_hour: int = Field(default=9, ge=0, le=23)
    optimize_time: bool = True
    version: int = Field(default=0, ge=0)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        return ContactInput.valid_timezone(value)

    @model_validator(mode="after")
    def safe_graph(self) -> Self:
        by_id = {node.id: node for node in self.nodes}
        if len(by_id) != len(self.nodes):
            raise ValueError("Node IDs must be unique")
        starts = [node for node in self.nodes if node.type == "start"]
        if len(starts) != 1 or not any(node.type == "end" for node in self.nodes):
            raise ValueError("Use exactly one start and at least one end")
        visited: set[str] = set()
        visiting: set[str] = set()

        def visit(key: str) -> None:
            if key in visiting:
                raise ValueError("Playbooks must not contain cycles")
            if key in visited:
                return
            if key not in by_id:
                raise ValueError("Every connection must point to an existing node")
            visiting.add(key)
            node = by_id[key]
            if node.type != "end" and not node.next:
                raise ValueError("Each step needs a next connection")
            if node.type == "condition" and not node.otherwise:
                raise ValueError("Conditions need a false branch")
            if node.type != "condition" and node.otherwise:
                raise ValueError("Only condition nodes can have a false branch")
            if node.type == "end" and (node.next or node.otherwise):
                raise ValueError("End nodes cannot have outgoing connections")
            for target in (node.next, node.otherwise):
                if target:
                    visit(target)
            visiting.remove(key)
            visited.add(key)

        visit(starts[0].id)
        if len(visited) != len(by_id):
            raise ValueError("Remove nodes that cannot be reached from start")
        return self
