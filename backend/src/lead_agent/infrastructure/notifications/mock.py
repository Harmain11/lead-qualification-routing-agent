from __future__ import annotations

from uuid import uuid4

from lead_agent.domain.routing import NotificationDelivery, NotificationMessage


class MockNotificationProvider:
    provider_name = "mock"

    def __init__(self, *, fail: bool = False, retryable: bool = True) -> None:
        self.fail = fail
        self.retryable = retryable
        self.sent: list[NotificationMessage] = []

    def send(self, message: NotificationMessage) -> NotificationDelivery:
        if self.fail:
            return NotificationDelivery(
                state="retryable" if self.retryable else "failed",
                error_message="Simulated notification provider failure",
                retryable=self.retryable,
            )
        self.sent.append(message)
        return NotificationDelivery(state="sent", external_id=f"mock-{uuid4()}")
