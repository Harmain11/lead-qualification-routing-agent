from datetime import UTC, datetime
from uuid import uuid4

import pytest

from lead_agent.domain.crm_inbound import ExternalCrmRecord
from lead_agent.domain.gmail import GmailEvidence, GmailLeadExtraction, GmailProspectCandidate
from lead_agent.infrastructure.gmail import GmailInboundProvider, _evidence_is_from_source


def _record(body: str) -> ExternalCrmRecord:
    return ExternalCrmRecord(
        provider="gmail", object_type="message", record_id="forwarded-thread",
        modified_at=datetime(2026, 10, 19, tzinfo=UTC),
        values={
            "headers": {"from": "Morgan West <morgan@agency.example>", "subject": "Discussion"},
            "thread_id": "forwarded-thread", "body": body, "thread_context": body,
        },
    )


def _provider(extractor) -> GmailInboundProvider:
    return GmailInboundProvider(
        uuid4(), "test-only", "inbox@agency.example",
        internal_domains={"agency.example", "agency-cloud.example"}, lead_extractor=extractor,
    )


def _extraction(candidates) -> GmailLeadExtraction:
    return GmailLeadExtraction(
        is_genuine_inquiry=True, reason="An early-stage commercial conversation.",
        candidates=candidates,
    )


def test_quoted_wrapped_evidence_is_contiguous_without_transport_prefixes():
    source = "> > Please contact Dr. Singh on Monday at\r\n> > 1-202-555-0138."
    assert _evidence_is_from_source(
        source, "Please contact Dr. Singh on Monday at 1-202-555-0138."
    )
    assert not _evidence_is_from_source(source, "Please contact Dr. Other on Monday")
    assert not _evidence_is_from_source("Please call\n> somebody else", "Please call me")


def test_request_for_context_with_unsettled_service_and_signature_name_is_a_lead():
    body = (
        "From: Morgan West <morgan@agency.example>\n"
        "To: <lee@studio.example>\nSubject: More information\n\n"
        "We build an AI layer for your existing systems.\n\n"
        "> Subject: More information\n> Date: Monday\n"
        '> From: "lee studio.example" <lee@studio.example>\n'
        "> To: Morgan West <morgan@agency.example>\n\n"
        "> Give me a little more context\n>\n> Lee Taylor\n"
        "> Creative Director / Owner\n> lee@studio.example\n"
    )
    candidate = GmailProspectCandidate(
        role="direct_prospect", is_genuine_inquiry=True, confidence=0.94,
        reason="Asked for more information about services.",
        inquiry_evidence="Give me a little more context",
        full_name="Lee Taylor", email="lee@studio.example",
        requested_service="A fully specified AI transformation package",
        evidence=[
            GmailEvidence(field="full_name", source="from_header", excerpt="Lee Taylor"),
            GmailEvidence(field="email", source="from_header", excerpt="To: <lee@studio.example>"),
            GmailEvidence(
                field="requested_service", source="body",
                excerpt="We build an AI layer for your existing systems.",
            ),
        ],
    )
    [lead] = _provider(lambda **_: _extraction([candidate])).normalize_records(_record(body))
    assert lead.normalized_payload["full_name"] == "Lee Taylor"
    assert lead.normalized_payload["email"] == "lee@studio.example"
    assert lead.normalized_payload["product_or_service"] == "Unknown"
    assert lead.normalized_payload["gmail_candidate_review_notes"]
    assert {item["source"] for item in lead.normalized_payload["gmail_evidence"]} == {"body"}


def test_quoted_referral_creates_phone_only_contact_and_keeps_referrer_as_context():
    body = (
        "From: Morgan West <morgan@agency.example>\n"
        "To: <alex@firm.example>\nSubject: Collaboration\n\n"
        "Thank you for connecting us. We will contact Dr. Singh.\n\n"
        "> Subject: Collaboration\n> Date: Monday\n"
        "> From: Alex Howard <alex@firm.example>\n"
        "> To: Casey <casey@agency-cloud.example>\n\n"
        "> Please contact Dr. Singh on Monday at\n> 1-202-555-0138.\n"
        "> They can speak with you about this matter.\n\n"
        "> From: Casey <casey@agency-cloud.example>\n"
        "> To: Alex Howard <alex@firm.example>\n> Subject: Collaboration\n\n"
        "> Let us discuss a potential tech collaboration between your firm\n"
        "> and our AI solutions."
    )
    referrer = GmailProspectCandidate(
        role="external_referrer", is_genuine_inquiry=True, confidence=0.95,
        reason="Redirected the team to the appropriate contact.",
        inquiry_evidence="Please contact Dr. Singh",
        full_name="Alex Howard", email="alex@firm.example",
        evidence=[
            GmailEvidence(field="full_name", source="from_header", excerpt="Alex Howard"),
            GmailEvidence(field="email", source="from_header", excerpt="alex@firm.example"),
        ],
    )
    referred = GmailProspectCandidate(
        role="referred_prospect", is_genuine_inquiry=True, confidence=0.93,
        reason="Explicit referral about the commercial matter.",
        inquiry_evidence="Please contact Dr. Singh on Monday at 1-202-555-0138.",
        full_name="Dr. Singh", phone="1-202-555-0138", referred_by="Alex Howard",
        company_name="Unverified firm name",
        evidence=[
            GmailEvidence(field="full_name", source="body", excerpt="Dr. Singh"),
            GmailEvidence(field="phone", source="body", excerpt="1-202-555-0138"),
            GmailEvidence(field="referred_by", source="from_header", excerpt="Alex Howard"),
        ],
    )
    provider = _provider(lambda **_: _extraction([referrer, referred]))
    [lead] = provider.normalize_records(_record(body))
    [again] = provider.normalize_records(_record(body))
    assert lead.provider_record_id == again.provider_record_id
    assert lead.normalized_payload["full_name"] == "Dr. Singh"
    assert lead.normalized_payload["phone"] == "1-202-555-0138"
    assert lead.normalized_payload["email"] == ""
    assert lead.normalized_payload["gmail_referred_by"] == "Alex Howard"
    assert lead.normalized_payload["gmail_candidate_role"] == "referred_prospect"
    assert lead.normalized_payload["company_name"] == "Unknown"


@pytest.mark.parametrize("repair_succeeds", [True, False])
def test_invalid_evidence_gets_one_source_grounded_repair_not_an_optional_field_skip(
    repair_succeeds,
):
    body = (
        "From: Avery Lane <avery@client.example>\n"
        "To: Morgan West <morgan@agency.example>\nSubject: Services\n\n"
        "Could you tell me more about your services?"
    )
    calls = []

    def extractor(**kwargs):
        calls.append(kwargs)
        candidate = GmailProspectCandidate(
            role="direct_prospect", is_genuine_inquiry=True, confidence=0.95,
            reason="Requested more information about services.",
            inquiry_evidence=(
                "Could you tell me more about your services?"
                if repair_succeeds and len(calls) == 2 else "A paraphrased request for information."
            ),
            full_name="Avery Lane", email="avery@client.example",
            requested_service="Unverified scope",
            evidence=[
                GmailEvidence(field="full_name", source="from_header", excerpt="Avery Lane"),
                GmailEvidence(field="email", source="from_header", excerpt="avery@client.example"),
            ],
        )
        return _extraction([candidate])

    provider = _provider(extractor)
    if repair_succeeds:
        [lead] = provider.normalize_records(_record(body))
        assert lead.normalized_payload["full_name"] == "Avery Lane"
    else:
        with pytest.raises(ValueError, match="inquiry_evidence_not_in_source") as error:
            provider.normalize_records(_record(body))
        assert "unverified requested service" not in str(error.value)
        assert body not in str(error.value)
    assert len(calls) == 2
    assert "validation-feedback" not in calls[0]["headers"]
    assert "inquiry_evidence_not_in_source" in calls[1]["headers"]["validation-feedback"]


def test_missing_phone_on_explicit_referral_can_be_recovered_without_inventing_email():
    body = (
        "From: Alex Howard <alex@firm.example>\n"
        "To: Morgan West <morgan@agency.example>\nSubject: AI services\n\n"
        "Please contact Dr. Singh at 1-202-555-0138 about our services discussion."
    )
    calls = []

    def extractor(**kwargs):
        calls.append(kwargs)
        evidence = [
            GmailEvidence(field="full_name", source="body", excerpt="Dr. Singh"),
            GmailEvidence(field="referred_by", source="body", excerpt="Alex Howard"),
        ]
        if len(calls) == 2:
            evidence.append(GmailEvidence(field="phone", source="body", excerpt="1-202-555-0138"))
        return _extraction([GmailProspectCandidate(
            role="referred_prospect", is_genuine_inquiry=True, confidence=0.95,
            reason="Explicitly referred for a services discussion.",
            inquiry_evidence="Please contact Dr. Singh",
            full_name="Dr. Singh", referred_by="Alex Howard",
            phone="1-202-555-0138" if len(calls) == 2 else None,
            evidence=evidence,
        )])

    [lead] = _provider(extractor).normalize_records(_record(body))
    assert len(calls) == 2
    assert lead.normalized_payload["phone"] == "1-202-555-0138"
    assert lead.normalized_payload["email"] == ""
    assert "name_or_supported_contact_missing" in calls[1]["headers"]["validation-feedback"]
