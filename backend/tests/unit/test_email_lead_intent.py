import pytest

from lead_agent.infrastructure.gmail import candidate_reason


@pytest.mark.parametrize("subject,body", [
    ("Project update", "Our company team finished the software project yesterday."),
    ("Your business", "We offer software services. Contact us to grow your business."),
    ("Meeting tomorrow", "Our team meeting is at 10 AM. Please reply."),
    ("Re: Pricing request", "Thanks, noted.\nOn Monday someone wrote:\nWe need pricing for software."),
    ("News from our team", "Our software solution is available. Book a demo today."),
    ("Personal note", "I need help moving this weekend."),
])
def test_unrelated_messages_do_not_create_leads(subject, body):
    assert candidate_reason(
        {"from": "Ada <ada@example.com>", "subject": subject}, body, "sales@corral.test"
    ) == "no_inquiry_signal"


@pytest.mark.parametrize("subject,body", [
    ("Need pricing", "Could you share pricing for your software?"),
    ("Demo request", "We would like a demo for our company."),
    ("A new project", "We are looking for software automation for our business."),
    ("Question", "Could you help us implement your solution?"),
])
def test_explicit_buying_inquiries_remain_candidates(subject, body):
    assert candidate_reason(
        {"from": "Ada <ada@example.com>", "subject": subject}, body, "sales@corral.test"
    ) is None


@pytest.mark.parametrize("header,value,reason", [
    ("list-id", "weekly-news", "bulk_or_transactional"),
    ("list-unsubscribe", "<https://example.com/remove>", "bulk_or_transactional"),
    ("precedence", "bulk", "bulk_or_transactional"),
    ("auto-submitted", "auto-replied", "automated_sender"),
])
def test_automated_headers_override_sales_words(header, value, reason):
    assert candidate_reason(
        {"from": "Ada <ada@example.com>", "subject": "Demo request", header: value},
        "We would like pricing for your software.",
        "sales@corral.test",
    ) == reason
