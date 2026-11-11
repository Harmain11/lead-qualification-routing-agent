from datetime import UTC, datetime
from uuid import uuid4

import pytest

from lead_agent.domain.crm_inbound import ExternalCrmRecord
from lead_agent.domain.gmail import GmailEvidence, GmailLeadExtraction, GmailProspectCandidate
from lead_agent.domain.normalization import normalize_lead
from lead_agent.infrastructure.gmail import (
    GmailInboundProvider,
    _evidence_is_from_source,
    _is_internal_address,
    _phone_evidence_is_from_source,
)


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


def test_phone_evidence_matches_display_link_and_country_code_formatting():
    source = (
        "> Please contact Dr. Singh on Monday at 1-202-324-3000\r\n"
        "> <(202)%20324-3000>."
    )
    excerpt = "(202)%20324-3000"
    assert _phone_evidence_is_from_source(source, excerpt)
    assert _phone_evidence_is_from_source(source, "202 324 3000")
    assert not _phone_evidence_is_from_source(source, "202 324 3001")
    assert _evidence_is_from_source(
        source, "Please contact Dr. Singh on Monday at 1-202-324-3000."
    )
    assert not _evidence_is_from_source(
        source.replace("%20324-3000", "%20324-3001"),
        "Please contact Dr. Singh on Monday at 1-202-324-3000.",
    )


def test_request_for_context_with_unsettled_service_and_signature_name_is_a_lead():
    body = (
        "From: Morgan West <morgan@agency.example>\n"
        "To: <lee@studio.example>\nSubject: More information\n\n"
        "I am with Lee Creative Studio, a digital marketing agency. "
        "Our team has 18 employees, our annual revenue is $2,400,000, "
        "our budget is $25,000, and we are based in Karachi, Pakistan. "
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
        company_name="Lee Creative Studio", industry="digital marketing",
        geography="Karachi, Pakistan", employee_count=18,
        annual_revenue="$2,400,000", budget="$25,000",
        requested_service="A fully specified AI transformation package",
        evidence=[
            GmailEvidence(field="full_name", source="from_header", excerpt="lee studio.example"),
            GmailEvidence(field="email", source="from_header", excerpt="To: <lee@studio.example>"),
            GmailEvidence(
                field="company_name", source="body", excerpt="I am with Lee Creative Studio",
            ),
            GmailEvidence(
                field="industry", source="body", excerpt="a digital marketing agency",
            ),
            GmailEvidence(
                field="geography", source="body", excerpt="Karachi, Pakistan",
            ),
            GmailEvidence(
                field="employee_count", source="body", excerpt="18 employees",
            ),
            GmailEvidence(
                field="annual_revenue", source="body", excerpt="$2,400,000",
            ),
            GmailEvidence(
                field="budget", source="body", excerpt="$25,000",
            ),
            GmailEvidence(
                field="requested_service", source="body",
                excerpt="We build an AI layer for your existing systems.",
            ),
        ],
    )
    [lead] = _provider(lambda **_: _extraction([candidate])).normalize_records(_record(body))
    assert lead.normalized_payload["full_name"] == "Lee Taylor"
    assert lead.normalized_payload["email"] == "lee@studio.example"
    assert lead.normalized_payload["company_name"] == "Lee Creative Studio"
    assert lead.normalized_payload["industry"] == "digital marketing"
    assert lead.normalized_payload["geography"] == "Karachi, Pakistan"
    assert lead.normalized_payload["employee_count"] == 18
    assert lead.normalized_payload["annual_revenue"] == "$2,400,000"
    assert lead.normalized_payload["budget"] == "$25,000"
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
        "> Please contact Dr. Singh on Monday at "
        "1-202-555-0138\n> <(202)%20555-0138>.\n"
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
            GmailEvidence(field="phone", source="subject", excerpt="(202)%20555-0138"),
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


@pytest.mark.parametrize("domain", [
    "ingenuitysys.com", "ingenuitysystem.com", "ingenuitysystems.com",
    "ingenuity-systems.cloud", "mail.ingenuity.example",
])
def test_ingenuity_company_domains_are_internal_without_manual_configuration(domain):
    assert _is_internal_address(f"rep@{domain}", set(), set())


@pytest.mark.parametrize("address", [
    "person@client.example", "person@notingenuity.com", "ingenuity@gmail.com",
])
def test_unrelated_addresses_are_not_internal_brand_matches(address):
    assert not _is_internal_address(address, set(), set())


def test_internal_message_without_external_candidate_is_never_imported_as_lead():
    body = (
        "From: Morgan West <morgan@agency.example>\n"
        "To: Reese Example <reese@agency.example>\n"
        "Subject: Internal planning\n\n"
        "Let's review the account plan before the customer call."
    )
    provider = _provider(lambda **_: _extraction([]))
    with pytest.raises(ValueError, match="internal_workspace_message"):
        provider.normalize_records(_record(body))


def test_nested_ingenuity_forwarder_and_named_internal_rep_are_not_leads():
    body = (
        "From: Reese Example <reese@ingenuity-systems.cloud>\n"
        "To: Lee Taylor <lee@client.example>\nSubject: Startup call\n\n"
        "Could we discuss our AI services?\n"
        "Reese Example\nIngenuity Systems\n1-202-555-0140\n\n"
        "> From: Lee Taylor <lee@client.example>\n"
        "> To: Reese Example <reese@ingenuity-systems.cloud>\n"
        "> Subject: Startup call\n\n> Give me a little more context\n"
    )
    lead = GmailProspectCandidate(
        role="direct_prospect", is_genuine_inquiry=True, confidence=0.95,
        reason="Engaged in the introductory service conversation.",
        inquiry_evidence="Give me a little more context",
        full_name="Lee Taylor", email="lee@client.example",
        evidence=[
            GmailEvidence(field="full_name", source="body", excerpt="Lee Taylor"),
            GmailEvidence(field="email", source="body", excerpt="lee@client.example"),
        ],
    )
    internal = GmailProspectCandidate(
        role="direct_prospect", is_genuine_inquiry=True, confidence=0.95,
        reason="Incorrectly selected the representative.",
        inquiry_evidence="Could we discuss our AI services?",
        full_name="Reese Example", phone="1-202-555-0140", company_name="Ingenuity Systems",
        evidence=[
            GmailEvidence(field="full_name", source="body", excerpt="Reese Example"),
            GmailEvidence(field="phone", source="body", excerpt="1-202-555-0140"),
            GmailEvidence(field="company_name", source="body", excerpt="Ingenuity Systems"),
        ],
    )
    observed = []

    def extractor(**kwargs):
        observed.append(kwargs["headers"])
        return _extraction([internal, lead])

    record = _record(body)
    record.values["headers"] = {
        "from": "Company Forwarder <forwarder@ingenuitysystem.com>",
        "subject": "Forwarded conversation",
    }
    [result] = _provider(extractor).normalize_records(record)
    assert result.normalized_payload["email"] == "lee@client.example"
    assert result.provider_metadata["original_sender_email"] == "forwarder@ingenuitysystem.com"
    assert not any(
        "ingenuity" in email
        for email in result.normalized_payload["gmail_eligible_participant_emails"]
    )
    assert "reese example" in result.normalized_payload["gmail_internal_participant_names"]
    assert observed[0]["internal-company-names"] == "Ingenuity,Ingenuity Systems"


def test_name_is_not_borrowed_from_a_different_correspondents_signature():
    body = (
        "From: Avery Lane <avery@client.example>\n"
        "To: Morgan <morgan@agency.example>\nSubject: Services\n\n"
        "Could we discuss your services?\n\n"
        "From: Other Person <other@elsewhere.example>\n"
        "To: Morgan <morgan@agency.example>\nSubject: Other matter\n\n"
        "Invented Name\nother@elsewhere.example\n"
    )
    candidate = GmailProspectCandidate(
        role="direct_prospect", is_genuine_inquiry=True, confidence=0.95,
        reason="Incorrectly combined identities from two messages.",
        inquiry_evidence="Could we discuss your services?",
        full_name="Invented Name", email="avery@client.example",
        evidence=[
            GmailEvidence(field="full_name", source="from_header", excerpt="Avery Lane"),
            GmailEvidence(field="email", source="from_header", excerpt="avery@client.example"),
        ],
    )
    with pytest.raises(ValueError, match="full_name"):
        _provider(lambda **_: _extraction([candidate])).normalize_records(_record(body))


def test_startup_reply_with_verified_email_and_missing_details_is_saved_as_unknown():
    body = (
        "From: <prospect@client.example>\n"
        "To: Morgan <morgan@agency.example>\nSubject: Intro call about our AI services\n\n"
        "Can you tell me more about your services?"
    )
    candidate = GmailProspectCandidate(
        role="direct_prospect", is_genuine_inquiry=True, confidence=0.95,
        reason="Early-stage services discussion with an identified external email.",
        inquiry_evidence="Can you tell me more about your services?",
        email="prospect@client.example",
        evidence=[
            GmailEvidence(field="email", source="from_header", excerpt="prospect@client.example")
        ],
    )
    [lead] = _provider(lambda **_: _extraction([candidate])).normalize_records(_record(body))
    assert lead.normalized_payload["email"] == "prospect@client.example"
    assert lead.normalized_payload["full_name"] == "Name not provided"
    assert "full_name" in lead.normalized_payload["gmail_unknown_identity_fields"]
    assert lead.normalized_payload["company_name"] == "Unknown"
    assert lead.normalized_payload["product_or_service"] == "Unknown"
    assert lead.normalized_payload["job_title"] is None
    saved = normalize_lead(lead.normalized_payload, uuid4())
    assert saved.email == "prospect@client.example"
    assert saved.full_name == "Name not provided"
