from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

IntegrationFlow = Literal[
    "automatic_inbound",
    "manual_outbound",
    "outbound_action",
    "request_response",
]


class IntegrationDirectionality(BaseModel):
    """Backend-owned capability policy for an integration category."""

    model_config = ConfigDict(frozen=True)

    automatic_inbound: bool = False
    manual_outbound: bool = False
    automatic_outbound: bool = False
    outbound_action: bool = False
    request_response: bool = False
    description: str


_CRM = IntegrationDirectionality(
    automatic_inbound=True,
    manual_outbound=True,
    description=(
        "CRM records enter automatically after inbound sync is enabled; writes back to the "
        "CRM require an explicit authorized user action."
    ),
)
_EMAIL = IntegrationDirectionality(
    automatic_inbound=True,
    description=(
        "Approved mailbox messages are inbound lead sources; the application does not send mail."
    ),
)
_NOTIFICATION = IntegrationDirectionality(
    outbound_action=True,
    description="Notifications are outbound delivery actions produced by routing policy.",
)
_ANALYSIS = IntegrationDirectionality(
    request_response=True,
    description="Analysis providers receive a bounded request and return structured analysis.",
)
_INBOUND = IntegrationDirectionality(
    automatic_inbound=True,
    description="This provider supplies inbound lead events only.",
)

PROVIDER_DIRECTIONALITY: dict[str, IntegrationDirectionality] = {
    "salesforce": _CRM,
    "hubspot": _CRM,
    "gmail": _EMAIL,
    "outlook": _EMAIL,
    "slack": _NOTIFICATION,
    "openai": _ANALYSIS,
    "linkedin": _INBOUND,
    "mock": _CRM,
}


def directionality_for(provider: str) -> IntegrationDirectionality:
    try:
        return PROVIDER_DIRECTIONALITY[provider]
    except KeyError as error:
        raise ValueError(
            f"No integration directionality policy for provider: {provider}"
        ) from error
