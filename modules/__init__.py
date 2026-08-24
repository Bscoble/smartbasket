"""
Grocery Gecko modules package.
Contains core business logic for pricing, sheets, barcode scanning, and feedback.
"""

from importlib import import_module


_EXPORTS = {
    "SheetsManager": ("modules.sheets", "SheetsManager"),
    "PriceScraper": ("modules.pricing", "PriceScraper"),
    "BarcodeScanner": ("modules.barcode", "BarcodeScanner"),
    "ProductLookup": ("modules.barcode", "ProductLookup"),
    "FeedbackManager": ("modules.feedback", "FeedbackManager"),
    "AuthManager": ("modules.auth", "AuthManager"),
}


def __getattr__(name):
    """Load public classes on demand without introducing import cycles."""
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = _EXPORTS[name]
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value

__all__ = [
    *_EXPORTS,
]
