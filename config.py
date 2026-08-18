"""
Configuration and constants for SmartBasket application.
Centralized settings for stores, colors, API endpoints, and application behavior.
"""

# ============================================================================
# STORE CONFIGURATION
# ============================================================================

STORES = {
    "Woolworths": {
        "color": "#005A36",
        "initial": "W",
        "api_actor": "stealth_mode/woolworths-product-search-scraper",
        "search_url": "https://www.woolworths.com.au/shop/search/products?searchTerm={}",
    },
    "Coles": {
        "color": "#E31837",
        "initial": "C",
        "api_actor": "stealth_mode/coles-product-search-scraper",
        "search_url": "https://www.coles.com.au/search/products?q={}",
    },
    "Aldi": {
        "color": "#002D62",
        "initial": "A",
        "search_url": "https://www.aldi.com.au/en/search/?q={}",
        "zenrows_required": True,
    },
    "IGA": {
        "color": "#E31837",
        "initial": "I",
        "search_url": "https://www.igashop.com.au/search?q={}",
        "zenrows_required": True,
    },
}

STORE_NAMES = list(STORES.keys())
DEFAULT_STORE_PREFS = {store: True for store in STORE_NAMES}

# ============================================================================
# PRICE CACHE CONFIGURATION
# ============================================================================

CACHE_EXPIRY_HOURS_VALID = 24  # Valid prices (< $90)
CACHE_EXPIRY_HOURS_INVALID = 1  # Invalid prices (>= $90)
PRICE_VALIDITY_THRESHOLD = 90.00  # USD threshold for valid pricing
DEFAULT_PRICE_FALLBACK = 99.99  # Fallback for scraping failures
APIFY_DEFAULT_PRICE = 5.00  # Fallback for Apify scraping failures

# ============================================================================
# PRICE CONSTRAINTS & VALIDATION
# ============================================================================

MIN_VALID_PRICE = 0.50
MAX_VALID_PRICE = 150.00

# ============================================================================
# API CONFIGURATION
# ============================================================================

ZENROWS_API_URL = "https://api.zenrows.com/v1/"
ZENROWS_PARAMS = {
    "js_render": "true",
    "antibot": "true",
    "premium_proxy": "true",
    "block_resources": "image,media,stylesheet,font",
}

APIFY_DEFAULT_CONFIG = {
    "ignore_url_failures": True,
    "max_items_per_url": 1,
    "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
}

# ============================================================================
# SCRAPING CONFIGURATION
# ============================================================================

REQUEST_TIMEOUT = 15  # seconds
THREAD_POOL_MAX_WORKERS = 4
THREAD_POOL_TIMEOUT = 30  # seconds

# ============================================================================
# PRODUCT DATABASE
# ============================================================================

OPEN_FOOD_FACTS_BARCODE_URL = "https://world.openfoodfacts.org/api/v2/product/{}.json"
OPEN_FOOD_FACTS_SEARCH_URL = "https://world.openfoodfacts.org/cgi/search.pl"
OPEN_FOOD_FACTS_USER_AGENT = "SmartBasketApp/1.0 (Australian Supermarket Price Tracker)"

# ============================================================================
# GOOGLE SHEETS CONFIGURATION
# ============================================================================

SPREADSHEET_ID = "1e_ZARwsDg0LTYfVkgFjybUDXluycHW79lz2ntwRxoaw"
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

WORKSHEET_NAMES = {
    "users": "Users",
    "shopping_list": "Shopping List",
    "price_cache": "Price Cache",
    "preferences": "Preferences",
    "recent_shops": "Recent Shops",
    "product_catalog": "Product Catalog",
}

WORKSHEET_CONFIG = {
    "users": {"rows": "1000", "cols": "9"},
    "price_cache": {"rows": "1000", "cols": "4"},
    "recent_shops": {"rows": "1000", "cols": "6"},
    "product_catalog": {"rows": "5000", "cols": "5"},
}

# ============================================================================
# HISTORY & RETENTION
# ============================================================================

RECENT_HISTORY_DAYS = 21  # Show items from last 21 days

# ============================================================================
# FEEDBACK & CONTACT
# ============================================================================

ADMIN_EMAIL = "bscoble74@gmail.com"
FEEDBACK_URL_TEMPLATE = "https://formsubmit.co/ajax/{}"
FEEDBACK_SUBJECT = "🚨 SmartBasket: New Problem Report"

# ============================================================================
# BARCODE DETECTION
# ============================================================================

BARCODE_BRIGHTNESS_THRESHOLD = 120  # Threshold for binarization pass

# ============================================================================
# UI CONFIGURATION
# ============================================================================

SUPPORTED_COUNTRIES = ["Australia", "New Zealand"]
DEFAULT_COUNTRY = "Australia"
COUNTRY_TIMEZONES = {
    "Australia": "Australia/Sydney",
    "New Zealand": "Pacific/Auckland",
}

APP_TITLE = "SmartBasket"
APP_ICON = "🛒"
APP_LAYOUT = "centered"
PHONE_FRAME_WIDTH = 412
PHONE_FRAME_HEIGHT = 850

# ============================================================================
# DATETIME
# ============================================================================

DATETIME_FORMAT = "%Y-%m-%d"
DATETIME_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# ============================================================================
# UNIT OPTIONS
# ============================================================================

UNIT_OPTIONS = ["Auto", "Each", "Litre", "Kilogram", "Gram", "Packet"]
UNIT_VALUES = {
    "Auto": "auto",
    "Each": "each",
    "Litre": "L",
    "Kilogram": "kg",
    "Gram": "g",
    "Packet": "Pk",
}

# ============================================================================
# REGEX PATTERNS
# ============================================================================

PRICE_REGEX = r"\$(\d+\.\d{2})"
FLOAT_REGEX = r"\d+\.\d{2}"
