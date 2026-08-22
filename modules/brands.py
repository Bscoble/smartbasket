"""Conservative brand resolution with auditable provenance."""

import re
from typing import Any, Dict, Optional


PRIVATE_LABELS = {
    "Woolworths": (
        "Woolworths",
        "Essentials",
        "Macro",
        "Farmers' Own",
    ),
    "Coles": (
        "Coles Finest",
        "Coles Simply",
        "Coles Perform",
        "Coles",
    ),
    "Aldi": (
        "Baker's Life",
        "Belmont",
        "Berg",
        "Brannan's Butchery",
        "Brookdale",
        "Casa Barelli",
        "Choceur",
        "Dairy Fine",
        "Di-San",
        "Emporium Selection",
        "Farmdale",
        "Goldenvale",
        "Hillcrest",
        "Lacura",
        "Logix",
        "Mamia",
        "Ocean Rise",
        "Ombra",
        "Power Force",
        "Remano",
        "Sprinters",
        "Tandil",
        "Tricare",
        "Westacre",
        "White Mill",
    ),
}

KNOWN_BRANDS = (
    "Arnott's",
    "Coca-Cola",
    "Cadbury",
    "Carman's",
    "Chobani",
    "Devondale",
    "Doritos",
    "Kellogg's",
    "Kettle",
    "Liddells",
    "Lindt",
    "Moccona",
    "Nestle",
    "Nutella",
    "Old El Paso",
    "Oreo",
    "Paul's",
    "Pepsi",
    "Sanitarium",
    "Smith's",
    "Sprite",
    "Tip Top",
    "Vegemite",
    "Vitasoy",
)

SOURCE_RANK = {
    "name_inference": 1,
    "private_label_mapping": 2,
    "retailer": 3,
    "manual": 4,
}


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _find_named_brand(product_name: str, brands: tuple[str, ...]) -> str:
    normalized_name = f" {_normalized(product_name)} "
    for brand in sorted(brands, key=len, reverse=True):
        if f" {_normalized(brand)} " in normalized_name:
            return brand
    return ""


def resolve_brand(
    product_name: str,
    explicit_brand: Optional[str] = None,
    store: str = "",
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """Return the strongest supported brand value and its provenance."""
    existing = existing or {}
    existing_brand = str(existing.get("brand") or "").strip()
    existing_source = str(existing.get("brand_source") or "").strip()
    existing_confidence = str(existing.get("brand_confidence") or "").strip()
    existing_rank = SOURCE_RANK.get(existing_source, 4 if existing_brand else 0)

    explicit_brand = str(explicit_brand or "").strip()
    if explicit_brand:
        candidate = {
            "brand": explicit_brand,
            "brand_source": "retailer",
            "brand_confidence": "high",
        }
    else:
        private_label = _find_named_brand(product_name, PRIVATE_LABELS.get(store, ()))
        if private_label:
            candidate = {
                "brand": private_label,
                "brand_source": "private_label_mapping",
                "brand_confidence": "high",
            }
        else:
            known_brand = _find_named_brand(product_name, KNOWN_BRANDS)
            candidate = {
                "brand": known_brand,
                "brand_source": "name_inference" if known_brand else "",
                "brand_confidence": "medium" if known_brand else "",
            }

    candidate_rank = SOURCE_RANK.get(candidate["brand_source"], 0)
    if existing_brand and existing_rank >= candidate_rank:
        return {
            "brand": existing_brand,
            "brand_source": existing_source,
            "brand_confidence": existing_confidence,
        }
    return candidate


def merge_brand_metadata(
    existing: Optional[Dict[str, Any]],
    candidate: Optional[Dict[str, Any]],
) -> Dict[str, str]:
    """Keep the stronger of two already-resolved brand metadata records."""
    existing = existing or {}
    candidate = candidate or {}
    existing_brand = str(existing.get("brand") or "").strip()
    candidate_brand = str(candidate.get("brand") or "").strip()
    existing_source = str(existing.get("brand_source") or "").strip()
    candidate_source = str(candidate.get("brand_source") or "").strip()
    existing_rank = SOURCE_RANK.get(existing_source, 4 if existing_brand else 0)
    candidate_rank = SOURCE_RANK.get(candidate_source, 4 if candidate_brand else 0)
    selected = existing if existing_brand and existing_rank >= candidate_rank else candidate
    return {
        "brand": str(selected.get("brand") or "").strip(),
        "brand_source": str(selected.get("brand_source") or "").strip(),
        "brand_confidence": str(selected.get("brand_confidence") or "").strip(),
    }