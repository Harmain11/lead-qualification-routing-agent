from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class GmailEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: Literal[
        "full_name", "email", "company_name", "job_title", "requested_service", "phone"
    ]
    source: Literal["subject", "body", "from_header", "reply_to"]
    excerpt: str = Field(min_length=1, max_length=500)


class GmailLeadExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_genuine_inquiry: bool
    reason: str = Field(min_length=1, max_length=500)
    inquiry_evidence: str = Field(default="", max_length=500)
    full_name: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=320)
    company_name: str | None = Field(default=None, max_length=255)
    job_title: str | None = Field(default=None, max_length=255)
    requested_service: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=64)
    message: str | None = Field(default=None, max_length=8000)
    evidence: list[GmailEvidence] = Field(default_factory=list, max_length=30)
