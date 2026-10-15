from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class EvidenceFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=500)
    excerpt: str = Field(min_length=1, max_length=500)
    source: str = Field(min_length=1, max_length=500)


class AgentBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=1800)
    recommendations: list[str] = Field(default_factory=list, max_length=5)
    facts: list[EvidenceFact] = Field(default_factory=list, max_length=20)
