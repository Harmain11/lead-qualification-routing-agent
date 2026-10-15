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
    automatic_outbound=True,
    description=(
        "Approved mailbox messages are inbound lead sources; the admin-connected Gmail sends "
        "one confirmation for stored leads with an email address."
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
_CALENDAR = IntegrationDirectionality(
    request_response=True,
    description=(
        "Availability is read on demand; creating a calendar event requires an explicit "
        "workspace admin confirmation."
    ),
)

PROVIDER_DIRECTIONALITY: dict[str, IntegrationDirectionality] = {
    "salesforce": _CRM,
    "hubspot": _CRM,
    "gmail": _EMAIL,
    "outlook": _EMAIL,
    "google_calendar": _CALENDAR,
    "calendly": _CALENDAR,
    "slack": _NOTIFICATION,
    "openai": _ANALYSIS,
    "gemini": _ANALYSIS,
    "claude": _ANALYSIS,
    "deepseek": _ANALYSIS,
    "xai": _ANALYSIS,
    "linkedin": _INBOUND,
    "instagram": _INBOUND,
    "messenger": _INBOUND,
    "whatsapp_business": _INBOUND,
    "clutch": _INBOUND,
    "mock": _CRM,
    "gohighlevel": _CRM,
    **{
        key: IntegrationDirectionality(
            request_response=True,
            outbound_action=True,
            description=(
                "Read call history on request. Outgoing calls require explicit rep "
                "confirmation and pass contact-permission checks."
            ),
        )
        for key in ("twilio", "aircall", "ringcentral", "vapi", "retell", "cloudtalk")
    },
}


def directionality_for(provider: str) -> IntegrationDirectionality:
    try:
        return PROVIDER_DIRECTIONALITY[provider]
    except KeyError as error:
        raise ValueError(
            f"No integration directionality policy for provider: {provider}"
        ) from error
