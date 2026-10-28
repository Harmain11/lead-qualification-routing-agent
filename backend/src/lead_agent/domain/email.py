PERSONAL_EMAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "yahoo.co.uk",
        "yahoo.ca",
        "yahoo.com.au",
        "yahoo.co.in",
        "yahoo.co.jp",
        "ymail.com",
        "rocketmail.com",
        "outlook.com",
        "outlook.co.uk",
        "outlook.fr",
        "outlook.de",
        "hotmail.com",
        "hotmail.co.uk",
        "hotmail.fr",
        "live.com",
        "live.co.uk",
        "msn.com",
        "icloud.com",
        "me.com",
        "mac.com",
        "aol.com",
        "proton.me",
        "protonmail.com",
        "pm.me",
        "gmx.com",
        "gmx.net",
        "mail.com",
        "fastmail.com",
        "hey.com",
        "zoho.com",
        "tuta.com",
        "tutanota.com",
        "yandex.com",
        "yandex.ru",
        "comcast.net",
        "verizon.net",
        "att.net",
        "sbcglobal.net",
        "bellsouth.net",
        "cox.net",
        "charter.net",
        "earthlink.net",
        "btinternet.com",
        "shaw.ca",
        "rogers.com",
    }
)


def is_personal_email_address(email: str) -> bool:
    """Identify common consumer mailbox providers without guessing from local parts."""

    _local, separator, domain = email.strip().casefold().rpartition("@")
    return bool(separator and domain.rstrip(".") in PERSONAL_EMAIL_DOMAINS)
