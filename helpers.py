"""
Helper functions and utilities for SmartBasket application.
Includes price formatting, data validation, and common UI utilities.
"""

from typing import Optional, Dict, Tuple
from config import STORES, UNIT_OPTIONS, COUNTRY_TIMEZONES, DEFAULT_COUNTRY
import re


def format_price(price: float) -> str:
    """
    Format a price value as a currency string.
    
    Args:
        price: Numeric price value
        
    Returns:
        Formatted price string (e.g., "$12.50")
    """
    return f"${price:.2f}"


def get_store_color(store_name: str) -> str:
    """
    Get the brand color for a given store.
    
    Args:
        store_name: Name of the store
        
    Returns:
        Hex color code for the store
    """
    return STORES.get(store_name, {}).get("color", "#555")


def get_store_initial(store_name: str) -> str:
    """
    Get the single-letter initial for a given store.
    
    Args:
        store_name: Name of the store
        
    Returns:
        Single uppercase letter
    """
    return STORES.get(store_name, {}).get("initial", "?")


def extract_price_from_text(text: str) -> Optional[float]:
    """
    Extract a price value from text using regex.
    Looks for patterns like $12.50.
    
    Args:
        text: Text to search for price
        
    Returns:
        Float price value or None if not found
    """
    match = re.search(r"\d+\.\d{2}", text)
    if match:
        try:
            return float(match.group())
        except ValueError:
            pass
    return None


def clean_price_text(text: str) -> str:
    """
    Clean price text by removing common symbols and whitespace.
    
    Args:
        text: Raw price text
        
    Returns:
        Cleaned text
    """
    return text.replace("$", "").strip()


def is_valid_price(price: float, min_price: float = 0.50, max_price: float = 150.00) -> bool:
    """
    Check if a price is within valid bounds.
    
    Args:
        price: Price to validate
        min_price: Minimum valid price
        max_price: Maximum valid price
        
    Returns:
        True if price is within bounds
    """
    return min_price <= price <= max_price


def parse_quantity(qty_str: str, default: int = 1) -> int:
    """
    Parse a quantity string to integer, with fallback to default.
    
    Args:
        qty_str: String representation of quantity
        default: Default value if parsing fails
        
    Returns:
        Parsed quantity or default value
    """
    try:
        return int(qty_str)
    except (ValueError, TypeError):
        return default


def infer_unit(item_name: str) -> str:
    """Infer a stored unit from common product-size and packaging terms."""
    text = item_name.lower().strip()
    if re.search(r"\b\d+(?:\.\d+)?\s*(?:litre|litres|liter|liters|l)\b", text):
        return "L"
    if re.search(r"\b\d+(?:\.\d+)?\s*(?:kilogram|kilograms|kilo|kilos|kg)\b", text):
        return "kg"
    if re.search(r"\b\d+(?:\.\d+)?\s*(?:gram|grams|g)\b", text):
        return "g"
    if re.search(r"\b\d+\s*(?:pack|packs|packet|packets|pk)\b", text):
        return "Pk"
    return "each"


def build_product_search_query(item_name: str, quantity, unit: str) -> str:
    """Add an explicit measurable size to a product query when one is selected."""
    unit_suffixes = {
        "Gram": "g",
        "Kilogram": "kg",
        "Litre": "L",
    }
    suffix = unit_suffixes.get(unit)
    if not suffix:
        return item_name.strip()

    numeric_quantity = float(quantity)
    formatted_quantity = (
        str(int(numeric_quantity))
        if numeric_quantity.is_integer()
        else str(numeric_quantity).rstrip("0").rstrip(".")
    )
    return f"{item_name.strip()} {formatted_quantity}{suffix}".strip()


def item_key(store: str, item_name: str) -> Tuple[str, str]:
    """
    Create a cache key tuple for store/item combinations.
    Ensures consistency in cache key generation.
    
    Args:
        store: Store name
        item_name: Item name
        
    Returns:
        Tuple of (store, item_name_lowercase)
    """
    return (store, item_name.lower())


def validate_email(email: str) -> bool:
    """
    Basic email validation.
    
    Args:
        email: Email address to validate
        
    Returns:
        True if email appears valid
    """
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return bool(re.match(pattern, email))


def validate_australian_postcode(postcode: str) -> bool:
    """
    Validate Australian postcode (4 digits, 0200-9999).
    
    Args:
        postcode: Postcode string
        
    Returns:
        True if valid Australian postcode
    """
    try:
        pc = int(postcode.strip())
        return 200 <= pc <= 9999
    except (ValueError, AttributeError):
        return False


BRAND_ALIASES = {
    "arnott's": ["arnott's", "arnotts", "arnott s"],
    "arnotts": ["arnotts", "arnott's", "arnott s"],
    "woolworths": ["woolworths", "woolworths select"],
    "coles": ["coles", "coles brand"],
    "aldi": ["aldi", "aldi exclusive"],
}


def normalize_search_query(item_name: str) -> str:
    """Normalize a product name for more reliable cross-retailer searches."""
    if not item_name:
        return ""

    text = item_name.strip()
    text = re.sub(r"\s+", " ", text)
    text = text.replace("&", " and ")
    text = text.replace("’", "'")
    text = re.sub(r"[^a-zA-Z0-9\s\-\.\'\"]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def expand_brand_aliases(text: str) -> list[str]:
    """Return common brand alias variants for a search query."""
    normalized = normalize_search_query(text)
    if not normalized:
        return []

    variants = [normalized]
    words = normalized.split()
    for idx, word in enumerate(words):
        compact = re.sub(r"[^a-z0-9]", "", word.lower())
        for canonical, aliases in BRAND_ALIASES.items():
            alias_tokens = {re.sub(r"[^a-z0-9]", "", alias.lower()) for alias in aliases}
            if compact in alias_tokens:
                for alias in aliases:
                    alias_value = alias
                    if word[:1].isupper():
                        alias_value = alias_value[0].upper() + alias_value[1:]
                    rebuilt = words.copy()
                    rebuilt[idx] = alias_value
                    variants.append(" ".join(rebuilt))
                break

    deduped = []
    for variant in variants:
        cleaned = " ".join(variant.split())
        if cleaned and cleaned not in deduped:
            deduped.append(cleaned)
    return deduped


def build_store_search_query(item_name: str, store_name: str) -> str:
    """Build a retailer-aware search string using product name, brand, and size cues."""
    candidates = build_store_search_candidates(item_name, store_name)
    return candidates[0] if candidates else ""


def build_store_search_candidates(item_name: str, store_name: str) -> list[str]:
    """Generate retailer-aware search candidates to retry when the first query misses."""
    q = normalize_search_query(item_name)
    if not q:
        return []

    store = store_name or ""
    words = q.split()
    if len(words) <= 2:
        return [q]

    preferred = []
    for word in words:
        if word.lower() in {"and", "the", "a", "an"}:
            if preferred and preferred[-1] not in {"and"}:
                preferred.append("and")
            continue
        preferred.append(word)

    candidates = []
    base = " ".join(preferred)
    if base:
        candidates.append(base)

    alias_variants = expand_brand_aliases(base)
    for alias_variant in alias_variants:
        if alias_variant and alias_variant not in candidates:
            candidates.append(alias_variant)

    # Fallback candidate: keep the core brand/product text without filler words.
    compact = " ".join(preferred[:min(8, len(preferred))])
    if compact and compact not in candidates:
        candidates.append(compact)

    # Stronger variant: prefer brand + size terms when present.
    size_tokens = []
    for idx, word in enumerate(preferred):
        if re.search(r"\d+(?:\.\d+)?(?:g|kg|ml|l|litre|litres|oz)", word.lower()):
            size_tokens.extend(preferred[max(0, idx - 1):min(len(preferred), idx + 2)])
            break
    if size_tokens:
        size_variant = " ".join(dict.fromkeys(size_tokens))
        if size_variant and size_variant not in candidates:
            candidates.append(size_variant)

    # Retailers often rank brand-first grocery queries more accurately.
    if "milk" in {word.lower() for word in preferred} and len(preferred) >= 3:
        brand = preferred[0]
        size_words = [word for word in preferred if re.search(r"\d+(?:\.\d+)?(?:g|kg|ml|l|litre|litres|oz)", word.lower())]
        size_word_set = {word.lower() for word in size_words}
        descriptors = [word for word in preferred[1:] if word.lower() not in {"milk", *size_word_set}]
        brand_first = " ".join([brand, *descriptors, "milk", *size_words])
        if brand_first and brand_first not in candidates:
            candidates.append(brand_first)

    # Another common variant: remove very generic words like "original" when they add noise.
    filtered = [word for word in preferred if word.lower() not in {"original", "classic", "fresh", "new"}]
    if len(filtered) > 1:
        clean_variant = " ".join(filtered[:min(10, len(filtered))])
        if clean_variant and clean_variant not in candidates:
            candidates.append(clean_variant)

    # Retailer-specific tuning.
    if store == "Aldi":
        candidates = [c for c in candidates if len(c.split()) <= 10]
    elif store in {"Woolworths", "Coles"}:
        candidates = [c for c in candidates if len(c.split()) <= 12]

    if not candidates:
        return [q]

    deduped = []
    for candidate in candidates:
        candidate = " ".join(candidate.split())
        if candidate and candidate not in deduped:
            deduped.append(candidate)
    return deduped[:5]


def build_search_url(store_name: str, query: str) -> str:
    """
    Build a search URL for a given store and query.
    
    Args:
        store_name: Name of the store
        query: Search query
        
    Returns:
        Complete search URL
    """
    from urllib.parse import quote
    store_config = STORES.get(store_name)
    if not store_config:
        return ""
    return store_config["search_url"].format(quote(query))


def get_greeting(country: str = DEFAULT_COUNTRY) -> str:
    """
    Get a time-based greeting.
    
    Returns:
        Greeting string (Good morning/afternoon/evening)
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    timezone_name = COUNTRY_TIMEZONES.get(country, COUNTRY_TIMEZONES[DEFAULT_COUNTRY])
    hour = datetime.now(ZoneInfo(timezone_name)).hour
    if hour < 12:
        return "Good morning,"
    elif hour < 18:
        return "Good afternoon,"
    else:
        return "Good evening,"


def sanitize_item_name(name: str) -> str:
    """
    Sanitize item name for safe storage.
    
    Args:
        name: Item name to sanitize
        
    Returns:
        Sanitized item name
    """
    return name.strip() if name else ""


def truncate_text(text: str, max_length: int = 50) -> str:
    """
    Truncate text to maximum length with ellipsis.
    
    Args:
        text: Text to truncate
        max_length: Maximum length
        
    Returns:
        Truncated text
    """
    if len(text) > max_length:
        return text[:max_length - 3] + "..."
    return text


def highlight_best_price_item(price: float, all_prices: list) -> bool:
    """
    Determine if a price is the best (lowest) among all prices.
    
    Args:
        price: Price to check
        all_prices: List of all prices
        
    Returns:
        True if this is the best price
    """
    if not all_prices:
        return False
    return price == min(all_prices)
