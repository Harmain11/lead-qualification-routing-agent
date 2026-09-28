from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

NotificationState = Literal["pending", "sent", "failed", "retryable", "suppressed"]
RoutePriority = Literal["urgent", "high", "normal", "low", "none"]


class ActionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    immediate_action: str
    response_priority: str
    talking_points: list[str] = Field(default_factory=list)
    unanswered_questions: list[str] = Field(default_factory=list)
    known_pain_point: str
    urgency_timeline: str
    potential_objections: list[str] = Field(default_factory=list)
    next_step_recommendation: str
    supporting_evidence: list[str] = Field(default_factory=list)
    follow_up_strategy: list[str] = Field(default_factory=list)
    conversion_goal: str = ""


class NotificationMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deduplication_key: str
    priority: RoutePriority
    text: str = Field(max_length=3000)
    metadata: dict[str, str] = Field(default_factory=dict)


class NotificationDelivery(BaseModel):
    state: NotificationState
    external_id: str | None = None
    error_message: str | None = None
    retryable: bool = False
    retry_after_seconds: int | None = None


class NotificationProvider(Protocol):
    provider_name: str

    def send(self, message: NotificationMessage) -> NotificationDelivery:
        """Deliver one idempotent notification or return a durable failure state."""


class RoutingDecision(BaseModel):
    lead_id: UUID
    qualification_id: UUID
    qualification_band: str
    assigned_queue: str
    assigned_rep_id: str | None
    assigned_rep_name: str | None
    routing_reason: str
    matched_rules: list[str]
    action_plan: ActionPlan
    priority: RoutePriority
    sla_target_at: datetime | None
    notification_state: NotificationState
    manual_review: bool
    routing_timestamp: datetime
