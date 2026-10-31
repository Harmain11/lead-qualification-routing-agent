import pytest
from pydantic import ValidationError

from lead_agent.domain.operations import CompanyInput, ContactInput, merge_record_profile


def test_contact_profile_accepts_known_fields_and_trims_tags():
    contact = ContactInput(
        name="Amara",
        profile={"department": " Revenue ", "tags": ["partner", "partner", " renewal "], "preferred_channel": "sms"},
    )
    assert contact.profile == {"department": "Revenue", "tags": ["partner", "renewal"], "preferred_channel": "sms"}


@pytest.mark.parametrize(
    "profile",
    [
        {"unknown": "x"},
        {"linkedin_url": "http://linkedin.com/in/x"},
        {"preferred_channel": "pigeon"},
        {"tags": ["x" * 41]},
    ],
)
def test_contact_profile_rejects_unsafe_values(profile):
    with pytest.raises(ValidationError):
        ContactInput(name="Amara", profile=profile)


def test_company_profile_year_must_be_a_year():
    assert CompanyInput(name="Acme", profile={"founded_year": "2014"}).profile == {"founded_year": "2014"}
    with pytest.raises(ValidationError):
        CompanyInput(name="Acme", profile={"founded_year": "soon"})


def test_merge_keeps_unrelated_keys_and_clears_empty_values():
    existing = {"cited_summary": "from enrichment", "notes": "old"}
    merged = merge_record_profile(existing, {"notes": "", "department": "Ops"})
    assert merged == {"cited_summary": "from enrichment", "department": "Ops"}
