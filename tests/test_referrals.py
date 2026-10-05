import os
import sys
from urllib.parse import parse_qs, urlsplit

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.referrals import build_email_draft, build_invitation, validate_public_app_url


def test_invitation_includes_public_link_and_user_name():
    message = build_invitation("Alex", 12.5, "https://example.com")
    assert "$12.50" in message
    assert "Cheers,\nAlex" in message
    assert message.endswith("https://example.com")
    assert "Brad" not in message


def test_invitation_without_deployment_has_no_dangling_link_label():
    message = build_invitation("", 0, "")
    assert "A Grocery Gecko shopper" in message
    assert "Try Grocery Gecko:" not in message


@pytest.mark.parametrize("url", [
    "https://github.com/Bscoble/smartbasket",
    "javascript:alert(1)",
    "https://example.com?auth_token=secret",
    "https://user:password@example.com",
    "https://example.com/#profile",
])
def test_public_url_rejects_repository_and_unsafe_links(url):
    with pytest.raises(ValueError):
        validate_public_app_url(url)


def test_public_url_allows_unconfigured_and_deployed_links():
    assert validate_public_app_url("") == ""
    assert validate_public_app_url(" https://example.com/app ") == "https://example.com/app"


def test_email_draft_preserves_message_and_encodes_special_characters():
    message = "Hello & welcome?\nCheers,\nAlex\nhttps://example.com"
    url = build_email_draft(" friend@example.com ", message)
    parsed = urlsplit(url)
    assert parsed.scheme == "mailto"
    assert parsed.path == "friend@example.com"
    assert parse_qs(parsed.query) == {"subject": ["Try Grocery Gecko"], "body": [message]}
