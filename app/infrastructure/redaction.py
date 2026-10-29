"""Mask documented credential and email patterns in fictional evidence text.

This is an explicit pattern filter, not a guarantee that arbitrary input is safe.
Unknown secret formats, encoded values and personal names remain outside it.
"""

import re
from collections.abc import Callable

REDACTED = "[REDACTED]"

_KEYED_SECRET = re.compile(
    r"(?P<prefix>(?<![\w-])[\"']?"
    r"(?:password|passwd|token|access_token|api[_-]?key|secret|client_secret)"
    r"[\"']?\s*[:=]\s*)"
    r"(?P<value>\"(?:\\[\s\S]|[^\"\\])*\"|'(?:\\[\s\S]|[^'\\])*'"
    r"|\"[^\r\n]*|'[^\r\n]*"
    r"|[^\s,;\"'<>{}\[\]]+)",
    re.IGNORECASE,
)
_URL_USERINFO = re.compile(
    r"(?P<prefix>(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]{0,31}://)"
    r"(?P<value>[^/\s?#]*@)"
)
_BEARER = re.compile(
    r"(?P<prefix>\bBearer[ \t]+)(?P<value>[A-Za-z0-9._~+/=-]+)",
    re.IGNORECASE,
)
_KNOWN_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_-])"
    r"(?:sk-(?:proj-)?[A-Za-z0-9_-]{8,}"
    r"|gh[pousr]_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]{8,})"
    r"(?![A-Za-z0-9_-])"
)
_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+-])"
    r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9]"
    r"(?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?\.[A-Za-z]{2,63}"
    r"(?![A-Za-z0-9-])"
)


def _mask_keyed_secret(match: re.Match[str]) -> str:
    """Preserve the secret key and matching value quotes while replacing content."""
    value = match["value"]
    quote = (
        value[0]
        if len(value) > 1 and value[0] in "\"'" and value[-1] == value[0]
        else ""
    )
    return match["prefix"] + quote + REDACTED + quote


def _mask_url_userinfo(match: re.Match[str]) -> str:
    """Remove URL usernames and passwords together while keeping the scheme."""
    return match["prefix"] + REDACTED + "@"


def _mask_bearer(match: re.Match[str]) -> str:
    """Retain the Bearer label without retaining the credential it introduces."""
    return match["prefix"] + REDACTED


def _mask_whole_match(match: re.Match[str]) -> str:
    """Replace an entire recognized token or email address with the marker."""
    return REDACTED


def _apply_rule(
    text: str,
    pattern: re.Pattern[str],
    replacement: Callable[[re.Match[str]], str],
) -> tuple[str, int]:
    """Count actual text replacements so filtering an existing marker is stable."""
    count = 0

    def replace_match(match: re.Match[str]) -> str:
        """Count only matches whose replacement changes the matched content."""
        nonlocal count
        masked = replacement(match)
        if masked != match[0]:
            count += 1
        return masked

    return pattern.sub(replace_match, text), count


def redact_text(text: str) -> tuple[str, int]:
    """Mask supported patterns before output truncation and return a change count."""
    if not isinstance(text, str):
        raise ValueError("Text to redact must be a string.")
    rules = (
        (_URL_USERINFO, _mask_url_userinfo),
        (_BEARER, _mask_bearer),
        (_KEYED_SECRET, _mask_keyed_secret),
        (_KNOWN_TOKEN, _mask_whole_match),
        (_EMAIL, _mask_whole_match),
    )
    total = 0
    for pattern, replacement in rules:
        text, count = _apply_rule(text, pattern, replacement)
        total += count
    return text, total


def sanitize_text(text: str, limit: int) -> tuple[str, int, bool]:
    """Redact before applying a visible length bound so no secret is cut in half."""
    redacted, replacements = redact_text(text)
    return redacted[:limit], replacements, len(redacted) > limit
