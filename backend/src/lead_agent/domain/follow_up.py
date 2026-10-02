from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FollowUpEmailContent(BaseModel):
    """Validated, user-reviewable follow-up copy. It never authorizes delivery."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    subject: str = Field(min_length=1, max_length=180)
    body: str = Field(min_length=10, max_length=5000)
    rationale: str = Field(min_length=1, max_length=600)

    @field_validator("subject", "body", "rationale")
    @classmethod
    def reject_blank_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Follow-up email content cannot be blank")
        return value.strip()
