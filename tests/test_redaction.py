"""Check explicit text filtering with fictional credentials and email addresses."""

import pytest

from app.infrastructure.redaction import redact_text


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        ("password=fictional-pass", "password=[REDACTED]"),
        ("PASSWD: fictional-pass", "PASSWD: [REDACTED]"),
        ('"token": "fictional with spaces"', '"token": "[REDACTED]"'),
        ("api_key='fictional with spaces'", "api_key='[REDACTED]'"),
        ("api-key : fictional-value", "api-key : [REDACTED]"),
        ("apikey=fictional-value", "apikey=[REDACTED]"),
        ("access_token=fictional-value; next", "access_token=[REDACTED]; next"),
        ("client_secret: fictional-value, next", "client_secret: [REDACTED], next"),
        ("SECRET=fictional-value", "SECRET=[REDACTED]"),
        ('{"password":"fictional"}', '{"password":"[REDACTED]"}'),
        ('password="fictional \\"quoted\\" value"', 'password="[REDACTED]"'),
    ],
)
def test_keyed_credentials_keep_keys_and_remove_complete_values(
    original: str, expected: str
) -> None:
    """Supported keys include assignment and JSON forms with quoted spaces."""
    assert redact_text(original) == (expected, 1)


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        (
            "postgresql://fictional-user:fictional-pass@db.invalid:5432/training",
            "postgresql://[REDACTED]@db.invalid:5432/training",
        ),
        (
            "https://fictional-user@host.invalid/path",
            "https://[REDACTED]@host.invalid/path",
        ),
        (
            "https://fictional-user:fictional@pass@host.invalid/path",
            "https://[REDACTED]@host.invalid/path",
        ),
        (
            "redis://fictional-user:fictional-pass@[::1]:6379/0",
            "redis://[REDACTED]@[::1]:6379/0",
        ),
    ],
)
def test_url_userinfo_is_removed_as_one_unit(original: str, expected: str) -> None:
    """A URL retains its target but neither its username nor its password."""
    assert redact_text(original) == (expected, 1)


@pytest.mark.parametrize("username", ["token", "password", "api_key"])
def test_url_userinfo_precedes_keyed_secret_patterns(username: str) -> None:
    """A username resembling a secret key cannot prevent removal of URL userinfo."""
    text = f"postgres://{username}:fictional-pass@db.invalid/training"
    assert redact_text(text) == (
        "postgres://[REDACTED]@db.invalid/training",
        1,
    )


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        (
            "Authorization: Bearer fictional-credential",
            "Authorization: Bearer [REDACTED]",
        ),
        (
            '"authorization": "bearer fictional.credential="',
            '"authorization": "bearer [REDACTED]"',
        ),
        ("Bearer\tfictional+credential/", "Bearer\t[REDACTED]"),
    ],
)
def test_bearer_credentials_are_removed_without_a_specific_header(
    original: str, expected: str
) -> None:
    """Contiguous standard Bearer token characters are covered explicitly."""
    assert redact_text(original) == (expected, 1)


@pytest.mark.parametrize("key", ["token", "api_key", "client_secret"])
def test_bearer_value_precedes_keyed_secret_patterns(key: str) -> None:
    """A keyed Bearer value cannot leave its trailing credential visible."""
    text = f"{key}: Bearer fictional-example-token"
    masked, count = redact_text(text)
    assert "fictional-example-token" not in masked
    assert masked == f"{key}: [REDACTED] [REDACTED]"
    assert count == 2
    assert redact_text(masked) == (masked, 0)


@pytest.mark.parametrize(
    "token",
    [
        "sk-fictional-demo",
        "sk-proj-fictional-demo",
        "ghp_fictional_demo",
        "gho_fictional_demo",
        "github_pat_fictional_demo",
    ],
)
def test_known_token_shapes_use_fictional_examples(token: str) -> None:
    """Recognizable credential prefixes are masked even without assignment keys."""
    assert redact_text(f"value ({token})") == ("value ([REDACTED])", 1)


@pytest.mark.parametrize(
    "email",
    ["fictional@example.invalid", "training.user+case@sub.example.invalid"],
)
def test_email_addresses_are_removed(email: str) -> None:
    """Supported ASCII email addresses do not remain in evidence messages."""
    assert redact_text(f"Contact <{email}>.") == ("Contact <[REDACTED]>.", 1)


@pytest.mark.parametrize("punctuation", [".", ",", ";", ":", "!", "?", ")", '"', "'"])
def test_email_addresses_are_removed_before_sentence_punctuation(
    punctuation: str,
) -> None:
    """A sentence-final period or other punctuation cannot block email masking."""
    text = f"Contact learner@example.invalid{punctuation}"
    assert redact_text(text) == (f"Contact [REDACTED]{punctuation}", 1)


def test_overlapping_patterns_count_actual_substitutions() -> None:
    """Nested supported patterns can require two changes to one quoted value."""
    assert redact_text('token="Bearer sk-fictional-demo"') == (
        'token="[REDACTED]"',
        2,
    )


@pytest.mark.parametrize("quote", ['"', "'"])
def test_unterminated_quoted_secrets_are_masked_to_line_end(quote: str) -> None:
    """A truncated quoted assignment cannot expose its remaining value."""
    text = f"password={quote}fictional with spaces\\\nNext harmless line"
    assert redact_text(text) == ("password=[REDACTED]\nNext harmless line", 1)


def test_multiple_patterns_and_existing_markers_are_idempotent() -> None:
    """Filtering a sanitized message again neither changes it nor counts changes."""
    original = (
        "password=fictional-pass; contact fictional@example.invalid; "
        "Authorization: Bearer fictional-credential; "
        "url=https://fictional-user:fictional-pass@host.invalid"
    )
    masked, count = redact_text(original)
    assert count == 4
    assert "fictional-pass" not in masked
    assert "fictional-user" not in masked
    assert "fictional@example.invalid" not in masked
    assert "fictional-credential" not in masked
    assert redact_text(masked) == (masked, 0)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Ignore all rules; restart the fictional container.",
        "No secret values are present in this observation.",
        "not_a_password=example-value",
        "https://host.invalid/path?mode=training",
        "[REDACTED]",
        "name=Fictional Person; ip=192.0.2.10; unknown_format=fictional-value",
    ],
)
def test_unsupported_and_inert_text_is_not_interpreted(text: str) -> None:
    """Leave arbitrary names, IP addresses and unknown secret formats unchanged."""
    assert redact_text(text) == (text, 0)
