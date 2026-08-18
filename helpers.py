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
