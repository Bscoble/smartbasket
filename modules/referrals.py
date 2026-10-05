"""Build web-app referral messages and email drafts without sending email."""

from urllib.parse import quote, urlencode, urlsplit


def validate_public_app_url(value: str) -> str:
    url = value.strip()
    if not url:
        return ""
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.hostname.lower() in {"github.com", "www.github.com"}
        or any(character.isspace() for character in url)
    ):
        raise ValueError(
            "PUBLIC_APP_URL must be the public HTTP(S) app URL, without credentials, "
            "query parameters or fragments; a GitHub repository URL is not an app URL."
        )
    return url


def build_invitation(first_name: str, savings: float, app_url: str) -> str:
    opening = (
        f"Hey, you should give this a try. It saved me ${savings:.2f} on this week's shop."
        if savings > 0
        else "Hey, you should give this a try. It helps me compare grocery prices before I shop."
    )
    signature = first_name.strip() or "A Grocery Gecko shopper"
    message = f"{opening}\n\nCheers,\n{signature}"
    if app_url:
        message += f"\n\nTry Grocery Gecko:\n{app_url}"
    return message


def build_email_draft(recipient: str, message: str) -> str:
    parameters = urlencode(
        {"subject": "Try Grocery Gecko", "body": message},
        quote_via=quote,
    )
    return f"mailto:{quote(recipient.strip(), safe='@')}?{parameters}"
