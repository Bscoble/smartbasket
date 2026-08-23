"""Shopping-list quantity and package-size helpers."""

import re
from typing import Tuple


def infer_quantity_and_unit(item_name: str) -> Tuple[int, str]:
    """Infer an integer product size and stored unit from a product title."""
    text = item_name.lower().strip()
    unit_patterns = (
        ("L", r"\b(\d+)\s*(?:litre|litres|liter|liters|l)\b"),
        ("kg", r"\b(\d+)\s*(?:kilogram|kilograms|kilo|kilos|kg)\b"),
        ("g", r"\b(\d+)\s*(?:gram|grams|g)\b"),
    )
    for unit, pattern in unit_patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1)), unit

    return 1, "each"


def shopping_pack_count(quantity: int, unit: str, stored_pack_count=None) -> int:
    """Return item count separately from a product's measurable package size."""
    if stored_pack_count not in (None, ""):
        try:
            return max(1, int(stored_pack_count))
        except (TypeError, ValueError):
            pass
    if unit in {"g", "kg", "L"}:
        return 1
    return max(1, int(quantity))


def shopping_quantity_label(
    quantity: int,
    unit: str,
    stored_pack_count=None,
) -> str:
    """Return a list quantity without repeating a product's package size."""
    if unit in {"g", "kg", "L"}:
        pack_count = shopping_pack_count(quantity, unit, stored_pack_count)
        return f"{pack_count} {'pack' if pack_count == 1 else 'packs'}"
    return f"{quantity} {unit}"


def price_quantity_multiplier(quantity: int, unit: str) -> int:
    """Return how many shelf-priced packs contribute to a shopping-list total."""
    return shopping_pack_count(quantity, unit)