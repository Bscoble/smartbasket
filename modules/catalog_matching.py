"""Conservative matching for prices already stored in the local catalogue."""

import re
from typing import Callable, Dict, Iterable, Optional, Tuple

from modules.brands import resolve_brand


_IGNORED_TERMS = {
    "a",
    "aldi",
    "an",
    "and",
    "coles",
    "flavour",
    "fresh",
    "new",
    "of",
    "the",
    "woolworths",
    "x",
}

_ALIASES = {
    "barbecue": "bbq",
    "biscuits": "biscuit",
    "cans": "can",
    "coca": "coke",
    "cola": "coke",
    "crackers": "cracker",
    "flavored": "flavour",
    "flavoured": "flavour",
    "potatoes": "potato",
    "sausages": "sausage",
    "tams": "tam",
    "tissues": "tissue",
    "tomatoes": "tomato",
    "vegetables": "vegetable",
}


def _format_size_number(value: float) -> str:
    return str(int(value)) if value.is_integer() else str(value).rstrip("0").rstrip(".")


def _canonical_metric_token(amount: float, unit: str) -> str:
    normalized_unit = unit.lower()
    normalized_amount = amount
    if normalized_unit == "kg":
        normalized_amount *= 1000
        normalized_unit = "g"
    elif normalized_unit == "l":
        normalized_amount *= 1000
        normalized_unit = "ml"
    return f"{_format_size_number(normalized_amount)}{normalized_unit}"


def _expand_multipack_sizes(text: str) -> str:
    additions = []
    for match in re.finditer(
        r"\b(\d+)\s*x\s*(\d+(?:\.\d+)?)\s*(g|kg|ml|l)\b",
        text,
    ):
        pack_count = int(match.group(1))
        unit_size = float(match.group(2))
        unit = match.group(3)
        total_size = pack_count * unit_size
        additions.append(f"{pack_count}pk")
        additions.append(_canonical_metric_token(total_size, unit))
    return f"{text} {' '.join(additions)}".strip() if additions else text


def _normalize(value: str) -> list[str]:
    text = value.lower().replace("'", "")
    text = re.sub(r"\bwashed\s+(?:&|and)\s+ready\s+to\s+cook\b", " ", text)
    text = _expand_multipack_sizes(text)
    text = re.sub(r"\b(\d+)\s*x\s*(\d+)\s*(?:pack|packs|pk)\b", r"\2pk \1", text)
    text = re.sub(r"\bx\s*(\d+)\s*(?:pack|packs|pk)\b", r"\1pk", text)
    text = re.sub(r"\b(\d+)\s*(?:pack|packs|pk)\b", r"\1pk", text)
    text = re.sub(r"\b(\d+)\s*ply\b", r"\1ply", text)
    text = re.sub(r"\b(\d+(?:\.\d+)?)\s*(?:grams?|g)\b", r"\1g", text)
    text = re.sub(r"\b(\d+(?:\.\d+)?)\s*(?:kilograms?|kilos?|kg)\b", r"\1kg", text)
    text = re.sub(r"\b(\d+(?:\.\d+)?)\s*(?:millilitres?|milliliters?|ml)\b", r"\1ml", text)
    text = re.sub(r"\b(\d+(?:\.\d+)?)\s*(?:litres?|liters?|l)\b", r"\1l", text)
    terms = re.sub(r"[^a-z0-9.]+", " ", text).split()
    normalized_terms = []
    for term in terms:
        if term in _IGNORED_TERMS:
            continue
        term = _ALIASES.get(term, term)
        metric_match = re.fullmatch(r"(\d+(?:\.\d+)?)(g|kg|ml|l)", term)
        if metric_match:
            term = _canonical_metric_token(
                float(metric_match.group(1)),
                metric_match.group(2),
            )
        normalized_terms.append(term)
    return normalized_terms


def _size_terms(terms: Iterable[str]) -> set[str]:
    size_tokens = set()
    for term in terms:
        metric_match = re.fullmatch(r"(\d+(?:\.\d+)?)(g|kg|ml|l)", term)
        if metric_match:
            size_tokens.add(
                _canonical_metric_token(
                    float(metric_match.group(1)),
                    metric_match.group(2),
                )
            )
            continue
        if re.fullmatch(r"\d+pk", term):
            size_tokens.add(term)
    return size_tokens


def _match_score(query: str, candidate: str) -> Optional[float]:
    query_brand = resolve_brand(query)["brand"]
    candidate_brand = resolve_brand(candidate)["brand"]
    if query_brand and query_brand != candidate_brand:
        return None

    query_terms = _normalize(query)
    candidate_terms = _normalize(candidate)
    if not query_terms or not candidate_terms:
        return None

    query_sizes = _size_terms(query_terms)
    candidate_sizes = _size_terms(candidate_terms)
    if query_sizes and not query_sizes.issubset(candidate_sizes):
        return None

    query_set = set(query_terms)
    candidate_set = set(candidate_terms)
    matched = query_set & candidate_set
    descriptive_query = query_set - query_sizes
    descriptive_matches = matched - query_sizes
    if not descriptive_query or not descriptive_matches:
        return None

    coverage = len(matched) / len(query_set)
    descriptive_coverage = len(descriptive_matches) / len(descriptive_query)
    minimum_coverage = 1.0 if len(descriptive_query) <= 3 else 0.6
    if descriptive_coverage < minimum_coverage or coverage < 0.6:
        return None

    exact_bonus = 100 if query_terms == candidate_terms else 0
    precision = len(matched) / len(candidate_set)
    return exact_bonus + (coverage * 20) + (precision * 10)


def find_local_price_matches(
    item_name: str,
    stores: Iterable[str],
    standard_prices: Dict[Tuple[str, str], dict],
    is_valid: Callable[[dict], bool],
    is_eligible: Callable[[str, dict], bool] = None,
) -> Dict[str, Tuple[Tuple[str, str], dict]]:
    """Return the best fresh local catalogue entry for each requested store."""
    requested_stores = set(stores)
    matches = {}
    scores = {}

    for key, entry in standard_prices.items():
        store, stored_item = key
        if (
            store not in requested_stores
            or not is_valid(entry)
            or (is_eligible is not None and not is_eligible(stored_item, entry))
        ):
            continue
        candidate = f"{stored_item} {entry.get('product_name', '')}".strip()
        score = _match_score(item_name, candidate)
        if score is None or score <= scores.get(store, float("-inf")):
            continue
        scores[store] = score
        matches[store] = (key, entry)

    return matches