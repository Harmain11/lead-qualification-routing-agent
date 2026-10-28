"""Opt-in semantic checks for workspace assistant providers using synthetic CRM data.

Run with:
    python -m lead_agent.evaluations.assistant_semantics --workspace-id UUID --run

The only live operation is the configured reasoning provider call. The API route is
exercised with synthetic context and a session that has no write methods.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any, Literal
from unittest.mock import patch
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.api.routes import lead_assistant
from lead_agent.api.routes.lead_assistant import AssistantChatRequest, AssistantMessage
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.models import ProviderConnectionORM
from lead_agent.infrastructure.db.session import get_session_factory
from lead_agent.infrastructure.reasoning.configuration import REASONING_PROVIDERS
from lead_agent.infrastructure.reasoning.deterministic import DeterministicReasoningProvider
from lead_agent.infrastructure.reasoning.factory import resolve_workspace_reasoning_provider
from lead_agent.settings.config import get_settings

_PERMISSIONS = frozenset({"agents.execute", "leads.view", "leads.edit"})
_SYNTHETIC_LEAD_ID = UUID("10000000-0000-4000-8000-000000000001")
_SYNTHETIC_PROFILE = {
    "business_profile": {
        "company_name": "Example Labs",
        "website": "https://example.test",
        "industry": "Software",
    },
    "email_signature": "",
}
_SUCCESS_CLAIM = re.compile(
    r"\b(?:I|we)\s+(?:have\s+)?(?:already\s+)?"
    r"(?:booked|sent|deleted|saved|created|updated|changed|completed)\b"
    r"|\b(?:it|that|the action)\s+(?:has been|was)\s+"
    r"(?:booked|sent|deleted|saved|created|updated|changed|completed)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    description: str
    messages: tuple[tuple[Literal["user", "assistant"], str], ...]
    expected_kind: str
    permissions: frozenset[str] = _PERMISSIONS
    matched_leads: tuple[dict[str, Any], ...] = ()
    similar_leads: tuple[dict[str, Any], ...] = ()
    expected_action_values: dict[str, Any] = field(default_factory=dict)
    expected_payload_values: dict[str, Any] = field(default_factory=dict)
    reply_must_match: str | None = None
    reply_must_not_match: str | None = None
    must_not_claim_completion: bool = False
    provider_call_expected: bool = True


def _lead_record(
    *, full_name: str = "Sam Lee", email: str = "sam@example.test",
) -> dict[str, Any]:
    return {
        "id": str(_SYNTHETIC_LEAD_ID),
        "name": full_name,
        "email": email,
        "company": "Example Labs",
    }


def evaluation_cases() -> tuple[EvalCase, ...]:
    """Conversation fixtures contain only fictional .test contacts and records."""
    sam = _lead_record()
    return (
        EvalCase(
            case_id="informal-partial-create",
            description="Create a lead from informal wording and only supplied details.",
            messages=(("user", "could you add a lead for Acme? that's all I know"),),
            expected_kind="create_lead",
            expected_payload_values={"company_name": "Acme"},
        ),
        EvalCase(
            case_id="comma-details-and-correction",
            description="Use comma-separated follow-up details and honor a name correction.",
            messages=(
                ("user", "Add a new lead."),
                (
                    "assistant",
                    "To create the new lead, what name and contact details should I use?",
                ),
                ("user", "Mia Chen (not Mia Chan), mia@example.test, Acme"),
            ),
            expected_kind="create_lead",
            expected_payload_values={
                "full_name": "Mia Chen",
                "email": "mia@example.test",
                "company_name": "Acme",
            },
        ),
        EvalCase(
            case_id="existing-record-update",
            description="Update a matched existing lead instead of creating a duplicate.",
            messages=(("user", "For existing lead Sam Lee, change his phone to 415-555-0134."),),
            expected_kind="update_lead_profile",
            matched_leads=(sam,),
            expected_action_values={"target_id": str(_SYNTHETIC_LEAD_ID)},
            expected_payload_values={"phone": "415-555-0134"},
        ),
        EvalCase(
            case_id="note-not-stage",
            description="Treat a narrative note as a note, not a sales-stage change.",
            messages=(("user", "Add a note to Sam Lee: interested in a pilot next quarter."),),
            expected_kind="add_lead_note",
            matched_leads=(sam,),
            expected_action_values={"target_id": str(_SYNTHETIC_LEAD_ID)},
            expected_payload_values={"note": "interested in a pilot next quarter"},
        ),
        EvalCase(
            case_id="typo-needs-confirmation",
            description="Ask before using a similar-name record for a requested update.",
            messages=(("user", "Update Sam Lei's phone to 415-555-0177."),),
            expected_kind="none",
            similar_leads=(sam,),
            reply_must_match=r"confirm|which lead|Sam Lee",
            provider_call_expected=False,
        ),
        EvalCase(
            case_id="changed-intent",
            description="A clear new request supersedes the earlier note request.",
            messages=(
                ("user", "Add a note to Sam Lee about our proposal."),
                ("assistant", "What should the note say?"),
                ("user", "Actually, don't add a note; mark Sam as proposal sent today."),
            ),
            expected_kind="record_lead_progress",
            matched_leads=(sam,),
            expected_action_values={"target_id": str(_SYNTHETIC_LEAD_ID)},
            expected_payload_values={"stage": "proposal_sent"},
        ),
        EvalCase(
            case_id="details-after-clarification",
            description="Carry an explicit create request through a clarification turn.",
            messages=(
                ("user", "Create a lead."),
                ("assistant", "To create the lead, what details do you have?"),
                ("user", "Noah Kim, noah@example.test, Northstar Labs"),
            ),
            expected_kind="create_lead",
            expected_payload_values={
                "full_name": "Noah Kim",
                "email": "noah@example.test",
                "company_name": "Northstar Labs",
            },
        ),
        EvalCase(
            case_id="permission-limit",
            description="Explain missing lead-edit access without proposing creation.",
            messages=(("user", "Create a lead for Acme."),),
            expected_kind="none",
            permissions=frozenset({"agents.execute", "leads.view"}),
            reply_must_match=r"lead.edit|edit access|permission|access",
        ),
        EvalCase(
            case_id="unsupported-delete",
            description="Do not propose a delete operation absent from assistant capabilities.",
            messages=(("user", "Delete the lead Sam Lee."),),
            expected_kind="none",
            matched_leads=(sam,),
            reply_must_match=r"cannot|can't|not supported|don't support|do not support",
        ),
        EvalCase(
            case_id="acknowledgement-is-not-success",
            description="Do not treat a thank-you as authorization or proof of a save.",
            messages=(
                ("user", "Add a note to Sam Lee."),
                ("assistant", "I can prepare that as an editable draft."),
                ("user", "Thanks."),
            ),
            expected_kind="none",
            must_not_claim_completion=True,
        ),
        EvalCase(
            case_id="pending-is-not-complete",
            description="Do not claim a meeting was booked when the UI outcome is pending.",
            messages=(
                ("assistant", "I booked the meeting for Sam Lee."),
                ("user", "The action still says pending. Is it actually booked?"),
            ),
            expected_kind="none",
            reply_must_match=r"pending|not booked|not yet|can't confirm|cannot confirm",
            must_not_claim_completion=True,
        ),
    )


class _SyntheticSession:
    """Only supports route reads against synthetic records; deliberately has no write API."""

    def begin_nested(self):
        return nullcontext()

    def scalar(self, _statement: object) -> UUID:
        # The route checks that synthetic matched lead IDs are available. No real
        # database lookup or mutation is performed for this synthetic session.
        return _SYNTHETIC_LEAD_ID


def _scenario_context(case: EvalCase) -> dict[str, Any]:
    return {
        "permissions": sorted(case.permissions),
        "workspace_profile": _SYNTHETIC_PROFILE,
        "context_warnings": [],
        "workspace_records": {
            "matched_leads": list(case.matched_leads),
            "similar_leads": list(case.similar_leads),
            "lead_match_confirmed": False,
        },
    }


def _read_path(value: dict[str, Any], path: str) -> Any:
    current: Any = value
    for item in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(item)
    return current


def _payload_value_matches(key: str, actual: Any, expected: Any) -> bool:
    if key == "note" and isinstance(actual, str) and isinstance(expected, str):
        return expected.casefold() in actual.casefold()
    return actual == expected


def _claims_completion(reply: str) -> bool:
    # Remove short clauses explicitly negated by the assistant before looking for
    # positive completion language, so "cannot confirm it was booked" is safe.
    unasserted = re.sub(
        r"\b(?:not|never|cannot|can't|couldn't|didn't|doesn't|hasn't|haven't|isn't)\b"
        r"[^.!?;]{0,120}",
        " ",
        reply,
        flags=re.IGNORECASE,
    )
    return bool(_SUCCESS_CLAIM.search(unasserted))


def _evaluate_response(case: EvalCase, response: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    action = response.get("action")
    if not isinstance(action, dict):
        return ["response did not contain a structured action"]
    if action.get("kind") != case.expected_kind:
        failures.append(
            f"expected action {case.expected_kind!r}, got {action.get('kind')!r}"
        )
    for key, expected in case.expected_action_values.items():
        actual = action.get(key)
        if actual != expected:
            failures.append(f"action.{key}: expected {expected!r}, got {actual!r}")

    payload: dict[str, Any] = {}
    if action.get("payload_json"):
        try:
            parsed = json.loads(action["payload_json"])
            if isinstance(parsed, dict):
                payload = parsed
            else:
                failures.append("action.payload_json was not a JSON object")
        except (TypeError, json.JSONDecodeError):
            failures.append("action.payload_json was not valid JSON")
    for key, expected in case.expected_payload_values.items():
        actual = _read_path(payload, key)
        if not _payload_value_matches(key, actual, expected):
            failures.append(f"payload.{key}: expected {expected!r}, got {actual!r}")

    reply = response.get("reply")
    if not isinstance(reply, str):
        failures.append("response did not contain a reply")
        reply = ""
    if case.reply_must_match and not re.search(case.reply_must_match, reply, re.I):
        failures.append(f"reply did not explain the expected condition ({case.reply_must_match})")
    if case.reply_must_not_match and re.search(case.reply_must_not_match, reply, re.I):
        failures.append(f"reply matched disallowed wording ({case.reply_must_not_match})")
    if case.must_not_claim_completion and _claims_completion(reply):
        failures.append("reply claimed a save, send, booking, or other action completed")
    return failures


def run_scenario(
    case: EvalCase,
    provider: Any,
    workspace_id: UUID,
) -> dict[str, Any]:
    """Exercise the actual workspace route, supplying only synthetic CRM context."""
    workspace = WorkspaceContext(
        workspace_id=workspace_id,
        actor_id="synthetic-evaluation",
        permissions=case.permissions,
    )
    session = _SyntheticSession()
    request = AssistantChatRequest(
        messages=[
            AssistantMessage(role=role, content=content)
            for role, content in case.messages
        ]
    )
    synthetic_context = _scenario_context(case)
    response: dict[str, Any]
    with (
        patch.object(
            lead_assistant,
            "build_workspace_assistant_context",
            return_value=synthetic_context,
        ),
        patch.object(
            lead_assistant,
            "build_workspace_record_context",
            return_value=synthetic_context["workspace_records"],
        ),
        patch.object(
            lead_assistant,
            "resolve_workspace_reasoning_provider",
            return_value=provider,
        ),
    ):
        try:
            response = lead_assistant.chat_with_workspace_assistant(
                request, session=session, workspace=workspace,  # type: ignore[arg-type]
            )
        except HTTPException as error:
            response = {
                "reply": "",
                "action": {},
                "_evaluation_error": f"route returned HTTP {error.status_code}",
            }
        except Exception as error:
            response = {
                "reply": "",
                "action": {},
                "_evaluation_error": f"route/provider failed ({type(error).__name__})",
            }
    failures = _evaluate_response(case, response)
    if response.get("_evaluation_error"):
        failures.insert(0, str(response["_evaluation_error"]))
    action_value = response.get("action", {})
    action = action_value if isinstance(action_value, dict) else {}
    return {
        "case": case.case_id,
        "description": case.description,
        "provider_call_expected": case.provider_call_expected,
        "passed": not failures,
        "failures": failures,
        "action": {
            "kind": action.get("kind"),
            "target_id": action.get("target_id"),
            "payload_json": action.get("payload_json"),
        },
        "reply_excerpt": str(response.get("reply", ""))[:240],
    }


def _configured_providers(session: Session, workspace_id: UUID) -> list[str]:
    configured = set(
        session.scalars(
            select(ProviderConnectionORM.provider_type)
            .where(
                ProviderConnectionORM.workspace_id == workspace_id,
                ProviderConnectionORM.provider_type.in_(REASONING_PROVIDERS),
                ProviderConnectionORM.status == "verified",
                ProviderConnectionORM.secret_reference.is_not(None),
            )
            .distinct()
        )
    )
    return [provider for provider in REASONING_PROVIDERS if provider in configured]


def evaluate_workspace(
    workspace_id: UUID,
    requested_provider: str | None = None,
) -> dict[str, Any]:
    """Run bounded synthetic scenarios against verified providers via the app factory."""
    settings = get_settings()
    if settings.llm_provider.strip().lower() != "workspace":
        raise RuntimeError(
            "The evaluation requires LLM_PROVIDER=workspace so credentials are resolved "
            "from verified workspace connections."
        )

    session_factory = get_session_factory()
    with session_factory() as session:
        providers = _configured_providers(session, workspace_id)
        if requested_provider:
            providers = [name for name in providers if name == requested_provider]
        if not providers:
            raise RuntimeError(
                "No matching verified reasoning provider is configured for this workspace."
            )

        provider_results = []
        for name in providers:
            provider = resolve_workspace_reasoning_provider(
                session,
                workspace_id,
                provider_type=name,
                include_inactive=True,
            )
            if isinstance(provider, DeterministicReasoningProvider):
                provider_results.append({
                    "provider": name,
                    "passed": False,
                    "error": (
                        "The verified provider could not be resolved through the secure "
                        "workspace credential path."
                    ),
                    "cases": [],
                })
                continue

            cases: list[dict[str, Any]] = [
                run_scenario(case, provider, workspace_id)
                for case in evaluation_cases()
            ]
            provider_results.append({
                "provider": name,
                "model": getattr(provider, "model_name", None),
                "passed": all(case["passed"] for case in cases),
                "pass_count": sum(case["passed"] for case in cases),
                "case_count": len(cases),
                "expected_provider_calls": sum(
                    case["provider_call_expected"] for case in cases
                ),
                "cases": cases,
            })
    return {
        "workspace_id": str(workspace_id),
        "data_source": "synthetic_only",
        "external_actions_executed": False,
        "providers": provider_results,
        "passed": bool(provider_results) and all(item["passed"] for item in provider_results),
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate workspace assistant language interpretation using synthetic "
            "CRM conversations. Provider calls may incur usage charges."
        )
    )
    parser.add_argument("--workspace-id", required=True, type=UUID)
    parser.add_argument(
        "--provider",
        choices=REASONING_PROVIDERS,
        help="Evaluate only this verified workspace provider (default: all verified providers).",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        required=True,
        help=(
            "Explicitly opt in to live reasoning-provider calls (up to 10 calls and "
            "11 synthetic scenarios for each of the five supported providers)."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = evaluate_workspace(args.workspace_id, args.provider)
    except Exception as error:
        # Do not print exception text: provider/client exceptions may contain
        # request details. Surface a stable, actionable category instead.
        print(json.dumps({
            "passed": False,
            "error": f"Evaluation stopped: {type(error).__name__}. Check workspace/provider setup.",
        }, indent=2))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
