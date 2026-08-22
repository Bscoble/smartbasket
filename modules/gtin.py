"""Validation and normalization for consumer product GTIN identifiers."""

import re


def normalize_gtin(value) -> str:
    """Return a valid GTIN-8/UPC-12/GTIN-13/GTIN-14, or an empty string."""
    if value is None or isinstance(value, bool):
        return ""
    digits = re.sub(r"\D", "", str(value))
    if len(digits) not in (8, 12, 13, 14):
        return ""

    payload = digits[:-1]
    check_digit = int(digits[-1])
    weighted_sum = sum(
        int(digit) * (3 if offset % 2 == 0 else 1)
        for offset, digit in enumerate(reversed(payload))
    )
    expected = (10 - weighted_sum % 10) % 10
    return digits if check_digit == expected else ""