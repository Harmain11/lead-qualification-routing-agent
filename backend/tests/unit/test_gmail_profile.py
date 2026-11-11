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


def test_explicit_company_phrase_is_stable_when_ai_also_returns_a_guess() -> None:
    messages = [{
        "direction": "inbound",
        "sender": "Harmain <harmain@example.com>",
        "body": (
            "I work in IT services. My company name is HR Tech. "
            "Can we discuss pricing?"
        ),
    }]
    stated_company, stated_industry = extract_stated_gmail_profile(
        messages,
        "harmain@example.com",
    )

    # The Gmail lead normalizer gives these explicit values precedence over
    # provider guesses, so a later AI response cannot replace HR Tech.
    assert stated_company == "HR Tech"
    assert stated_industry == "IT services"


def test_extracts_company_and_industry_from_common_email_phrasing() -> None:
    messages = [{
        "direction": "inbound",
        "sender": "Harmain <harmain@example.com>",
        "body": (
            "I work at Harmain Construction as a project director. "
            "We operate in the construction industry."
        ),
    }]
    assert extract_stated_gmail_profile(messages, "harmain@example.com") == (
        "Harmain Construction", "construction",
    )


def test_extracts_industry_from_company_description() -> None:
    messages = [{
        "direction": "inbound",
        "sender": "Harmain <harmain@example.com>",
        "body": "We are a construction company looking for help with our website.",
    }]
    assert extract_stated_gmail_profile(messages, "harmain@example.com") == (
        None, "construction",
    )


def test_ignores_other_senders_and_quoted_text() -> None:
    messages = [
        {
            "direction": "outbound",
            "sender": "team@example.com",
            "body": "My company name is Wrong Co.",
        },
        {
            "direction": "inbound",
            "sender": "other@example.com",
            "body": "My company name is Other Co.",
        },
        {
            "direction": "inbound",
            "sender": "lead@example.com",
            "body": "Thanks.\n> My company name is Quoted Co.",
        },
    ]
    assert extract_stated_gmail_profile(messages, "lead@example.com") == (None, None)
