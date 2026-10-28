from lead_agent.application.gmail_profile import extract_stated_gmail_profile


def test_extracts_company_and_industry_stated_by_the_lead() -> None:
    messages = [{
        "direction": "inbound",
        "sender": "Harmain <harmain@example.com>",
        "body": (
            "Hey,\nI am an IT business working in IT services. "
            "My company name is HR tech. What would your basic plan cost?\n"
            "Regards,\nHarmain\nHR Tech"
        ),
    }]
    assert extract_stated_gmail_profile(messages, "harmain@example.com") == (
        "HR tech", "IT services",
    )


def test_ignores_other_senders_and_quoted_text() -> None:
    messages = [
        {"direction": "outbound", "sender": "team@example.com", "body": "My company name is Wrong Co."},
        {"direction": "inbound", "sender": "other@example.com", "body": "My company name is Other Co."},
        {"direction": "inbound", "sender": "lead@example.com", "body": "Thanks.\n> My company name is Quoted Co."},
    ]
    assert extract_stated_gmail_profile(messages, "lead@example.com") == (None, None)
