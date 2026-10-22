"""Security helpers.

# STUB: owned by Security. Minimal versions with the contract signatures; the owner's real
# version replaces them.
"""
from __future__ import annotations

import re

_SSN = re.compile(r"\b(\d{3})[- ]?(\d{2})[- ]?(\d{4})\b")
_LONG_NUMBER = re.compile(r"\b\d{8,17}\b")


def validate_ssn(text: str) -> tuple[bool, str]:
    match = _SSN.search(text or "")
    if not match:
        return False, "That doesn't look like a nine-digit Social Security number."
    area = int(match.group(1))
    if not 900 <= area <= 999:
        return False, "This practice system only accepts test numbers that start with 9."
    return True, f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def redact_pii(text: str) -> str:
    text = _SSN.sub("[SSN redacted]", text or "")
    return _LONG_NUMBER.sub("[number redacted]", text)


def scan_untrusted_text(text: str) -> list[str]:
    patterns = ["ignore previous", "ignore all", "system prompt", "you are now"]
    lowered = (text or "").lower()
    return [pattern for pattern in patterns if pattern in lowered]
