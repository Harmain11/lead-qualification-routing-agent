from uuid import uuid4

from lead_agent.api.schemas import LeadCreateRequest
from lead_agent.domain.normalization import normalize_lead


def test_normalization_produces_canonical_values() -> None:
    lead = normalize_lead(
        {
            "name": "  Jane   Doe ",
            "email": " JANE@EXAMPLE.COM ",
            "company": "  Acme   Inc ",
            "product": "Lead routing",
            "inquiry": " Need help with our workflow. ",
            "source": "website",
        },
        uuid4(),
    )

    assert lead.full_name == "Jane Doe"
    assert lead.email == "jane@example.com"
    assert lead.company_name == "Acme Inc"
    assert lead.product_or_service == "Lead routing"
    assert lead.message == "Need help with our workflow."
    assert lead.normalization_version == "1.0"


def test_normalization_rejects_missing_required_fields() -> None:
    try:
        normalize_lead({"email": "jane@example.com"}, uuid4())
    except ValueError as error:
        assert "required" in str(error)
    else:
        raise AssertionError("expected required-field validation error")


def test_manual_lead_request_accepts_partial_details() -> None:
    request = LeadCreateRequest.model_validate(
        {
            "source_detail": "manual-control-center",
            "source": "website",
            "full_name": "Kathi Sitek",
            "company_name": "Diversified Members Credit Union",
        }
    )
    lead = normalize_lead(request.model_dump(mode="json"), uuid4())

    assert lead.full_name == "Kathi Sitek"
    assert lead.source == "manual"
    assert lead.email == ""
    assert lead.company_name == "Diversified Members Credit Union"
    assert lead.product_or_service == ""
    assert lead.message == ""


def test_empty_manual_lead_is_rejected() -> None:
    try:
        normalize_lead(
            {"source_detail": "manual-control-center"},
            uuid4(),
        )
    except ValueError as error:
        assert "at least one known lead detail" in str(error)
    else:
        raise AssertionError("expected empty manual lead validation error")


def test_normalization_allows_missing_optional_fields() -> None:
    lead = normalize_lead(
        {
            "full_name": "Jane Doe",
            "email": "jane@example.com",
            "company_name": "Acme",
            "product_or_service": "Lead routing",
            "message": "Please share an overview.",
        },
        uuid4(),
    )
    assert lead.phone is None
    assert lead.job_title is None
    assert lead.employee_count is None
    assert lead.budget is None


def test_normalization_preserves_missing_identity_for_manual_review() -> None:
    lead = normalize_lead(
        {
            "email": "jane@example.com",
            "product_or_service": "Lead routing",
            "message": "Please share an overview.",
        },
        uuid4(),
    )

    assert lead.full_name == "Name not provided"
    assert lead.company_name == "Company not provided"


def test_normalization_rejects_malformed_employee_count_and_empty_message() -> None:
    base = {
        "full_name": "Jane Doe",
        "email": "jane@example.com",
        "company_name": "Acme",
        "product_or_service": "Lead routing",
        "message": "Please share an overview.",
    }
    try:
        normalize_lead(base | {"employee_count": "many"}, uuid4())
    except ValueError as error:
        assert "employee_count" in str(error)
    else:
        raise AssertionError("expected malformed employee count error")

    try:
        normalize_lead(base | {"message": "   "}, uuid4())
    except ValueError as error:
        assert "required" in str(error)
    else:
        raise AssertionError("expected empty message error")
