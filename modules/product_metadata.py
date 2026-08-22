"""Product-detail metadata extraction for retailer pages."""

import json
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict

import requests
from bs4 import BeautifulSoup

from config import REQUEST_TIMEOUT, ZENROWS_API_URL, ZENROWS_PARAMS


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def parse_allergen_statement(statement: str) -> Dict[str, str]:
    """Separate explicit contains and may-contain claims without inference."""
    cleaned = _clean_text(statement)
    contains_match = re.search(
        r"\bcontains\s*:\s*(.*?)(?=\bmay\s+contain\s*:|$)",
        cleaned,
        flags=re.IGNORECASE,
    )
    may_contain_match = re.search(
        r"\bmay\s+contain\s*:\s*(.*)$",
        cleaned,
        flags=re.IGNORECASE,
    )
    return {
        "allergens_raw": cleaned,
        "allergens_contains": _clean_text(contains_match.group(1)) if contains_match else "",
        "allergens_may_contain": _clean_text(may_contain_match.group(1)) if may_contain_match else "",
    }


def _find_key(data: Any, names: set[str]) -> Any:
    if isinstance(data, dict):
        for key, value in data.items():
            normalized_key = re.sub(r"[^a-z]", "", str(key).lower())
            if normalized_key in names and value not in (None, "", [], {}):
                return value
        for value in data.values():
            found = _find_key(value, names)
            if found not in (None, "", [], {}):
                return found
    elif isinstance(data, list):
        for value in data:
            found = _find_key(value, names)
            if found not in (None, "", [], {}):
                return found
    return None


def _section_text(soup: BeautifulSoup, label: str) -> str:
    label_pattern = re.compile(rf"^{re.escape(label)}$", re.IGNORECASE)
    heading = soup.find(lambda tag: tag.name and label_pattern.match(tag.get_text(" ", strip=True)))
    if not heading:
        return ""

    texts = []
    for sibling in heading.find_all_next():
        if sibling is heading:
            continue
        if sibling.name in {"h1", "h2", "h3", "h4", "summary"}:
            break
        if sibling.name not in {"p", "li", "table"}:
            continue
        sibling_text = _clean_text(sibling.get_text(" ", strip=True))
        if not sibling_text:
            continue
        if sibling.find_parent(lambda tag: tag is not None and tag is heading):
            continue
        texts.append(sibling_text)
        if len(" ".join(texts)) >= 4000:
            break
    return _clean_text(" ".join(dict.fromkeys(texts)))


def parse_woolworths_product_metadata(html: str, source_url: str = "") -> Dict[str, Any]:
    """Extract auditable metadata from a Woolworths product-detail page."""
    soup = BeautifulSoup(html or "", "html.parser")
    structured_payloads = []
    for script in soup.find_all("script", type={"application/ld+json", "application/json"}):
        try:
            structured_payloads.append(json.loads(script.string or script.get_text()))
        except (TypeError, ValueError):
            continue

    ingredients = _clean_text(_find_key(structured_payloads, {"ingredients", "ingredientstatement"}))
    allergens_raw = _clean_text(_find_key(structured_payloads, {"allergens", "allergenstatement"}))
    allergens_contains = _clean_text(_find_key(structured_payloads, {"allergencontains"}))
    allergens_may_contain = _clean_text(_find_key(structured_payloads, {"allergenmaycontain"}))
    country_of_origin = _clean_text(
        _find_key(structured_payloads, {"countryoforigin", "origincountry", "countryofmanufacture"})
    )
    nutrition = _find_key(
        structured_payloads,
        {"nutrition", "nutritioninformation", "nutritionalinformation", "nutritionfacts"},
    )
    if isinstance(nutrition, str) and nutrition.lstrip().startswith(("{", "[")):
        try:
            nutrition = json.loads(nutrition)
        except ValueError:
            pass

    ingredients = ingredients or _section_text(soup, "Ingredients")
    allergens_raw = allergens_raw or _section_text(soup, "Allergens")
    country_of_origin = country_of_origin or _section_text(soup, "Country of origin")
    allergen_fields = parse_allergen_statement(allergens_raw)
    if allergens_contains:
        allergen_fields["allergens_contains"] = allergens_contains
    if allergens_may_contain:
        allergen_fields["allergens_may_contain"] = allergens_may_contain
    if not allergen_fields["allergens_raw"]:
        explicit_statements = []
        if allergens_contains:
            explicit_statements.append(f"Contains: {allergens_contains}")
        if allergens_may_contain:
            explicit_statements.append(f"May contain: {allergens_may_contain}")
        allergen_fields["allergens_raw"] = " ".join(explicit_statements)

    status = "complete" if ingredients and (
        allergen_fields["allergens_contains"] or allergen_fields["allergens_may_contain"]
    ) else "partial" if any((ingredients, allergens_raw, country_of_origin, nutrition)) else "unavailable"
    return {
        "ingredients_raw": ingredients,
        **allergen_fields,
        "nutrition_json": json.dumps(nutrition, ensure_ascii=True, sort_keys=True) if nutrition else "",
        "country_of_origin": country_of_origin,
        "source_retailer": "Woolworths",
        "source_url": source_url,
        "extraction_status": status,
    }


def select_metadata_candidates(
    standard_prices: Dict,
    existing_metadata: Dict[str, Dict[str, Any]],
    limit: int,
    max_age_days: int,
    failed_retry_days: int,
    now: datetime = None,
) -> list[Dict[str, Any]]:
    """Select distinct Woolworths detail pages that are missing or stale."""
    now = now or datetime.now()
    candidates = {}
    for (store, _item), entry in standard_prices.items():
        source_url = str(entry.get("source_url") or "").strip()
        if store != "Woolworths" or not source_url:
            continue
        barcode = str(entry.get("barcode") or "").strip()
        metadata_key = barcode or source_url
        current = existing_metadata.get(metadata_key)
        if current:
            last_verified = current.get("last_verified")
            retry_days = (
                failed_retry_days
                if current.get("extraction_status") == "unavailable"
                else max_age_days
            )
            if last_verified and now - last_verified < timedelta(days=retry_days):
                continue
        candidates.setdefault(metadata_key, {
            "barcode": barcode,
            "canonical_name": entry.get("product_name", ""),
            "brand": entry.get("brand", ""),
            "source_retailer": store,
            "source_url": source_url,
        })
    return list(candidates.values())[:limit]


def fetch_woolworths_product_metadata(
    source_url: str,
    zenrows_key: str,
) -> tuple[Dict[str, Any], float]:
    """Fetch and parse one Woolworths product-detail page through ZenRows."""
    started_at = time.monotonic()
    response = requests.get(
        ZENROWS_API_URL,
        params={
            "apikey": zenrows_key,
            "url": source_url,
            **ZENROWS_PARAMS,
            "wait": "4000",
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return parse_woolworths_product_metadata(response.text, source_url), round(time.monotonic() - started_at, 3)