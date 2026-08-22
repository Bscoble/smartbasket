"""Positive dietary-claim checks for customer-facing product filtering."""

import re
from typing import Dict, Optional


GLUTEN_FREE = "Gluten Free"


def has_gluten_free_claim(*values) -> bool:
    """Return true only when an explicit 'gluten free' phrase is present."""
    return any(
        re.search(r"\bgluten[\s-]+free\b", str(value or ""), flags=re.IGNORECASE)
        for value in values
    )


def metadata_for_product(entry: dict, metadata: Dict[str, dict]) -> Optional[dict]:
    """Join product metadata by barcode first, then source URL."""
    barcode = str(entry.get("barcode") or "").strip()
    source_url = str(entry.get("source_url") or "").strip()
    return metadata.get(barcode) or metadata.get(source_url)


def product_is_gluten_free(item_name: str, entry: dict, metadata: Dict[str, dict]) -> bool:
    """Require an explicit gluten-free claim in descriptors or allergen metadata."""
    product_metadata = metadata_for_product(entry, metadata) or {}
    return has_gluten_free_claim(
        item_name,
        entry.get("product_name"),
        product_metadata.get("canonical_name"),
        product_metadata.get("allergens_raw"),
        product_metadata.get("allergens_contains"),
        product_metadata.get("allergens_may_contain"),
    )