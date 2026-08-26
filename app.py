"""
Grocery Gecko - Australian Grocery Price Comparison Application
A Streamlit-based app that helps users compare prices across major supermarkets.
"""

import base64
import html
import logging
import os
import pathlib
import sys
import time
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote
import streamlit as st
from PIL import Image
from streamlit_webrtc import WebRtcMode, webrtc_streamer
import gspread
from gspread.exceptions import APIError, SpreadsheetNotFound
from google.oauth2.service_account import Credentials

# Import configuration and modules
import config
APP_TITLE = getattr(config, "APP_TITLE", "Grocery Gecko")
APP_ICON = getattr(config, "APP_ICON", "🦎")
APP_LAYOUT = getattr(config, "APP_LAYOUT", "centered")
BRAND_NAME = getattr(config, "BRAND_NAME", APP_TITLE)
BRAND_TAGLINE = getattr(
    config,
    "BRAND_TAGLINE",
    "Compare your list. Keep the difference.",
)
GOOGLE_SCOPES = config.GOOGLE_SCOPES
SPREADSHEET_ID = config.SPREADSHEET_ID
APP_VERSION = getattr(config, "APP_VERSION", "Beta 1.0")
from helpers import (
    format_price,
    get_store_color,
    get_store_initial,
    get_greeting,
    validate_email,
    build_product_search_query,
)
from modules import SheetsManager, PriceScraper, BarcodeScanner, ProductLookup, FeedbackManager, AuthManager
from modules.catalog_matching import find_local_price_matches
from modules.dietary import has_gluten_free_claim, product_is_gluten_free
from modules.failed_scans import FailedScanStore, encode_failed_scan
from modules.gtin import normalize_gtin
from modules.shopping import (
    infer_quantity_and_unit,
    mark_all_items_collected,
    shopping_checkbox_keys,
    shopping_pack_count,
    shopping_quantity_label,
)

# ============================================================================
# LOGGING SETUP
# ============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
logger.info("Grocery Gecko application starting")

# ============================================================================
# PAGE CONFIGURATION
# ============================================================================
st.set_page_config(page_title=APP_TITLE, page_icon=APP_ICON, layout=APP_LAYOUT)

BRAND_MARK_PATH = pathlib.Path(__file__).parent / "static" / "grocery-gecko-mark.svg"
BRAND_MARK_DATA_URI = (
    "data:image/svg+xml;base64,"
    + base64.b64encode(BRAND_MARK_PATH.read_bytes()).decode("ascii")
)


def _build_sheets_connection_error(error: Exception) -> tuple[str, list[str]]:
    """Return a user-facing error summary and troubleshooting steps."""
    error_text = str(error).lower()

    if isinstance(error, KeyError) or "gcp_service_account" in error_text:
        return (
            "Database configuration is missing.",
            [
                "Add the 'gcp_service_account' secret to Streamlit secrets.",
                "If running locally, create .streamlit/secrets.toml with the service account fields.",
                "If on StreamlCloud, open App Settings > Secrets and paste the same values.",
            ],
        )

    if isinstance(error, SpreadsheetNotFound):
        return (
            "Database spreadsheet could not be opened.",
            [
                "Confirm SPREADSHEET_ID is correct in config.py.",
                "Share the spreadsheet with the service-account client_email as Editor.",         "Ensure the Users, Shopping List, and Price Cache sheets are accessible.",
            ],
        )

    if isinstance(error, APIError):
        if "quota" in error_text or "429" in error_text:
            return (
                "Google Sheets quota limit was reached.",
                [
                    "Wait 60-90 seconds and try again.",
                    "Reduce repeated reads (short-lived caching is now enabled in this app).",
                    "If this keeps happening, increase Google Sheets API quota for this project.",
                ],
            )
        return (
            "Google Sheets API rejected the request.",
            [
                "Enable Google Sheets API and Google Drive API for the service account project.",
                "Check whether quota limits or permissions are blocking requests.",
                "Verify the service account JSON is valid and active.",
            ],
        )

    if "private_key" in error_text or "service account" in error_text:
        return (
            "Service account credentials look invalid.",
            [
                "Re-copy the full service account JSON fields into Streamlit secrets.",
                "Ensure private_key contains newline escapes exactly as provided by Google.",
                "Regenerate a new key in Google Cloud if the current key was revoked.",
            ],
        )

    return (
        "Failed to connect to database.",
        [
            "Refresh and try again.",
            "If it keeps failing, verify secrets and Google Sheets access settings.",
        ],
    )

# ============================================================================
# AUTHENTICATION & SHEETS INITIALIZATION
# ============================================================================
if "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST"):
    sheets_manager = None
    auth_manager = None
    failed_scan_store = None
    logger.info("Running under pytest: skipping live Sheets initialization")
else:
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = Credentials.from_service_account_info(creds_dict, scopes=GOOGLE_SCOPES)
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(SPREADSHEET_ID)
        sheets_manager = SheetsManager(sh)
        auth_manager = AuthManager(sh)
        failed_scan_store = FailedScanStore(creds)
        logger.info("Google Sheets authenticated successfully")
    except Exception as e:
        logger.error(f"Failed to authenticate with Google Sheets: {e}", exc_info=True)
        summary, steps = _build_sheets_connection_error(e)
        st.error(summary)
        st.markdown("**How to fix this**")
        for step in steps:
            st.write(f"- {step}")
        with st.expander("Technical details"):
            st.write(f"Error type: {type(e).__name__}")
            st.write(f"Message: {e}")
            st.write(f"Spreadsheet ID: {SPREADSHEET_ID}")
        st.stop()

# Initialize API credentials for price scraping
if "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST"):
    price_scraper = None
else:
    try:
        ZENROWS_KEY = st.secrets.get("ZENROWS_KEY", "")
        APIFY_TOKEN = st.secrets.get("APIFY_TOKEN", "")
        price_scraper = PriceScraper(APIFY_TOKEN, ZENROWS_KEY)
        price_scraper.usage_logger = lambda **kw: sheets_manager.log_scrape_run(source="live_app", **kw)
        logger.info("Price scraper initialized")
    except Exception as e:
        logger.error(f"Failed to initialize price scraper: {e}", exc_info=True)
        price_scraper = None

# ============================================================================
# EXTERNAL FUNCTIONS - Moved to Modules
# ============================================================================
# Core business logic has been refactored into separate modules:
# - modules/sheets.py: Google Sheets operations via SheetsManager
# - modules/pricing.py: Price scraping via PriceScraper
# - modules/barcode.py: Barcode scanning via BarcodeScanner, ProductLookup
# - modules/feedback.py: Feedback submission via FeedbackManager
# See respective modules for implementation details and documentation.



# ============================================================================
# SESSION STATE INITIALIZATION
# ============================================================================
if "app_started" not in st.session_state:
    # Query parameters persist the welcome-screen decision across Streamlit sessions.
    st.session_state["app_started"] = st.query_params.get("welcome_seen") == "1"
if "authenticated" not in st.session_state:
    auth_token = st.query_params.get("auth_token", "")
    restored_user = auth_manager.validate_session(auth_token) if auth_manager is not None and auth_token else None
    st.session_state["authenticated"] = restored_user is not None
    st.session_state["current_user"] = restored_user
if "auth_mode" not in st.session_state:
    st.session_state["auth_mode"] = "login"
if "current_page" not in st.session_state:
    st.session_state["current_page"] = "home"
if "reset_email" not in st.session_state:
    st.session_state["reset_email"] = ""
if "auth_notice" not in st.session_state:
    st.session_state["auth_notice"] = ""
if "prefs" not in st.session_state:
    try:
        if sheets_manager is not None:
            st.session_state["prefs"] = sheets_manager.load_store_preferences()
        else:
            st.session_state["prefs"] = config.DEFAULT_STORE_PREFS
    except Exception as e:
        logger.error(f"Error loading store preferences: {e}")
        st.session_state["prefs"] = config.DEFAULT_STORE_PREFS
if "last_savings" not in st.session_state:
    st.session_state["last_savings"] = 0.0
if "expander_toggle" not in st.session_state:
    st.session_state["expander_toggle"] = False
if "search_results" not in st.session_state:
    st.session_state["search_results"] = []
if "search_performed" not in st.session_state:
    st.session_state["search_performed"] = False
if "recent_shops_available" not in st.session_state:
    st.session_state["recent_shops_available"] = False

if st.query_params.get("logout") == "1":
    logout_token = st.query_params.get("auth_token", "")
    if logout_token and auth_manager is not None:
        auth_manager.revoke_session(logout_token)
    st.query_params.pop("logout", None)
    st.query_params.pop("auth_token", None)
    st.session_state["authenticated"] = False
    st.session_state["current_user"] = None
    st.session_state["auth_mode"] = "login"
    st.session_state["current_page"] = "home"
    st.rerun()

if st.session_state["authenticated"] and st.query_params.get("profile") == "1":
    st.query_params.pop("profile", None)
    st.session_state["current_page"] = "profile"

prefs = {
    store_name: bool(st.session_state["prefs"].get(store_name, True))
    for store_name in config.STORE_NAMES
}
st.session_state["prefs"] = prefs

# ============================================================================
# HELPER FUNCTIONS FOR REPORT GENERATION
# ============================================================================


def split_shopping_available(report: dict) -> bool:
    """Return whether split-store shopping offers a materially different outcome."""
    item_breakdown = report.get("item_breakdown", [])
    if len(item_breakdown) < 2:
        return False

    cheapest_stores = {
        item.get("cheapest_store")
        for item in item_breakdown
        if item.get("cheapest_store")
    }
    return len(cheapest_stores) > 1


def single_store_shopping_available(report: dict) -> bool:
    """Return whether one store can supply the complete priced basket."""
    return bool(
        report.get("comparison_modes", {})
        .get("single_store_best", {})
        .get("is_complete", False)
    )


def resolve_scanned_product(
    sheets: SheetsManager,
    barcode: str,
    gluten_free_only: bool = False,
) -> Optional[dict]:
    """Resolve a barcode locally first, then reconcile an external fallback locally."""
    normalized_barcode = normalize_gtin(barcode)
    if not normalized_barcode:
        return None

    local_product = sheets.find_product_by_barcode(normalized_barcode)
    if local_product:
        if not gluten_free_only or product_is_gluten_free(
            local_product["title"],
            local_product,
            sheets.load_product_metadata(),
        ):
            return {**local_product, "source": "local_barcode"}
        return None

    product_name, product_image = ProductLookup.lookup_barcode_product(normalized_barcode)
    if not product_name:
        return None

    if gluten_free_only:
        local_matches = sheets.search_scraped_products(
            product_name,
            limit=1,
            gluten_free_only=True,
        )
    else:
        local_matches = sheets.search_scraped_products(product_name, limit=1)
    if local_matches:
        return {
            **local_matches[0],
            "barcode": normalized_barcode,
            "source": "local_name_match",
        }

    fallback = {
        "title": product_name,
        "image_url": product_image or "",
        "category": "",
        "subcategory": "",
        "brand": "",
        "stores": [],
        "barcode": normalized_barcode,
        "source": "open_food_facts",
    }
    return fallback if not gluten_free_only or has_gluten_free_claim(product_name) else None


def summarize_store_health(diagnostics: list) -> dict:
    """Return a concise per-store health summary for scraper diagnostics."""
    summary = {}
    for detail in diagnostics:
        store = detail.get("store", "Unknown")
        status = detail.get("status", "unavailable")
        entry = summary.setdefault(store, {"total": 0, "timeout": 0, "not_found": 0, "other": 0})
        entry["total"] += 1

        if status == "timeout":
            entry["timeout"] += 1
        elif status in {"not_found", "no_match", "unavailable"}:
            entry["not_found"] += 1
        else:
            entry["other"] += 1
    return summary


def generate_smart_basket_report(user_items: list, selected_stores: list) -> Optional[dict]:
    """
    Generate a comprehensive price comparison report for shopping items.
    
    Args:
        user_items: List of items from shopping list
        selected_stores: List of selected stores for comparison
        
    Returns:
        Dictionary with comparison results or None if no valid items
    """
    store_totals = {store: 0.0 for store in selected_stores}
    store_price_counts = {store: 0 for store in selected_stores}
    item_breakdown = []
    unpriced_items = []
    split_store_total = 0.0
    unavailable_reasons = []
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    # Filter valid items
    valid_items = [row for row in user_items if len(row) >= 3 and row[0].strip()]
    total_items = len(valid_items)
    if total_items == 0:
        return None
    
    status_text.text("Loading local prices...")
    price_cache = sheets_manager.load_price_cache()
    daily_specials = sheets_manager.load_daily_specials()
    standard_prices = sheets_manager.load_standard_prices()
    
    for idx, row in enumerate(valid_items):
        item_name = row[0]
        try:
            qty = int(row[1])
        except ValueError:
            qty = 1
        unit = row[2]
        pack_count = shopping_pack_count(qty, unit, row[4] if len(row) >= 5 else None)
        
        item_lower = item_name.lower()
        stores_to_search = selected_stores.copy()
        
        # Check if item mentions a specific store
        if "woolworths" in item_lower and "Woolworths" in selected_stores:
            stores_to_search = ["Woolworths"]
        elif "coles" in item_lower and "Coles" in selected_stores:
            stores_to_search = ["Coles"]
        elif "aldi" in item_lower and "Aldi" in selected_stores:
            stores_to_search = ["Aldi"]
        
        item_store_prices = {}
        item_store_status = {}
        local_matches = find_local_price_matches(
            item_name,
            stores_to_search,
            standard_prices,
            sheets_manager.is_standard_price_valid,
        )
        
        # Check cache and determine which stores need fresh prices
        for store in selected_stores:
            if store in stores_to_search:
                cache_key = (store, item_lower)
                special_data = daily_specials.get(cache_key)
                matched_key, standard_data = local_matches.get(store, (cache_key, None))
                special_data = daily_specials.get(matched_key) or special_data
                cached_data = price_cache.get(cache_key)
                
                if special_data and special_data.get("price") is not None:
                    item_store_prices[store] = special_data["price"]
                    item_store_status[store] = {
                        "status": "special",
                        "message": "Today's special price",
                        "product_name": special_data.get("product_name") or item_name,
                    }
                    logger.debug(f"Using special price for {item_name} at {store}")
                elif standard_data and sheets_manager.is_standard_price_valid(standard_data):
                    item_store_prices[store] = standard_data["price"]
                    item_store_status[store] = {
                        "status": "standard",
                        "message": "Standard shelf price",
                        "product_name": standard_data.get("product_name") or item_name,
                    }
                    logger.debug(f"Using standard price for {item_name} at {store}")
                elif (
                    cached_data
                    and cached_data.get("price", config.DEFAULT_PRICE_FALLBACK) < config.PRICE_VALIDITY_THRESHOLD
                    and sheets_manager.is_cache_valid(cached_data)
                ):
                    item_store_prices[store] = cached_data["price"]
                    item_store_status[store] = {
                        "status": "cached",
                        "message": "Using a cached price",
                        "product_name": cached_data.get("product_name") or item_name,
                    }
                    logger.debug(f"Using cached price for {item_name} at {store}")
                else:
                    item_store_prices[store] = None
                    item_store_status[store] = {
                        "status": "not_found",
                        "message": "No fresh local catalogue match",
                    }
            else:
                item_store_prices[store] = None
                item_store_status[store] = {
                    "status": "not_requested",
                    "message": "Not searched for this item",
                }
        
        # Calculate store totals and item breakdown
        item_store_data = {}
        for store, unit_price in item_store_prices.items():
            if unit_price is None:
                unavailable_reasons.append({
                    "item": item_name,
                    "store": store,
                    "status": item_store_status.get(store, {}).get("status", "unavailable"),
                    "message": item_store_status.get(store, {}).get("message", "Price unavailable"),
                })
                item_store_data[store] = {
                    "unit_price": "Unavailable",
                    "total_price": None,
                    "status": item_store_status.get(store, {}).get("status", "unavailable"),
                    "message": item_store_status.get(store, {}).get("message", "Price unavailable"),
                    "product_name": item_store_status.get(store, {}).get("product_name"),
                }
                continue

            total_price = unit_price * pack_count
            store_totals[store] += total_price
            store_price_counts[store] += 1
            item_store_data[store] = {
                "unit_price": format_price(unit_price) + f"/{unit}",
                "total_price": total_price,
                "status": item_store_status.get(store, {}).get("status", "ok"),
                "message": item_store_status.get(store, {}).get("message", "Price found"),
                "product_name": item_store_status.get(store, {}).get("product_name") or item_name,
            }
        
        sorted_item_stores = sorted(
            item_store_data.items(),
            key=lambda x: x[1]["total_price"] if x[1]["total_price"] is not None else float("inf"),
        )
        available_item_stores = [
            item for item in sorted_item_stores if item[1]["total_price"] is not None
        ]
        if not available_item_stores:
            logger.warning("No prices available for %s", item_name)
            unpriced_items.append(item_name)
            progress_bar.progress((idx + 1) / total_items)
            continue

        cheapest_store = available_item_stores[0][0]
        best_price = available_item_stores[0][1]["total_price"]
        highest_available_price = available_item_stores[-1][1]["total_price"]
        savings_vs_highest = (
            round(max(0.0, highest_available_price - best_price), 2)
            if len(available_item_stores) > 1
            else 0.0
        )
        
        split_store_total += best_price
        
        item_breakdown.append({
            "item_name": item_name,
            "quantity": f"{qty} {unit}",
            "cheapest_store": cheapest_store,
            "unit_price": format_price(best_price / pack_count),
            "total_price": format_price(best_price),
            "savings_vs_highest": savings_vs_highest,
            "price_options_count": len(available_item_stores),
            "all_stores": sorted_item_stores,
        })
        
        progress_bar.progress((idx + 1) / total_items)
    
    status_text.empty()
    progress_bar.empty()
    
    # Generate rankings, retaining stores with partial coverage for visibility.
    available_stores = [
        store for store in selected_stores if store_price_counts[store] > 0
    ]
    if not available_stores:
        logger.warning("No stores returned usable prices for this comparison")
        st.session_state["comparison_diagnostics"] = unavailable_reasons
        return None

    complete_stores = [
        store for store in available_stores
        if store_price_counts[store] == total_items
    ]
    complete_stores.sort(key=lambda store: store_totals[store])
    ranked_stores = sorted(
        ((store, store_totals[store]) for store in available_stores),
        key=lambda entry: (store_price_counts[entry[0]] < total_items, entry[1]),
    )
    best_single_store = complete_stores[0] if complete_stores else None
    if best_single_store:
        best_single_store_cost = store_totals[best_single_store]
        worst_complete_cost = max(store_totals[store] for store in complete_stores)
        trip_savings = max(0.0, worst_complete_cost - split_store_total)
    else:
        best_single_store_cost = None
        trip_savings = 0.0
    
    store_rankings = []
    for rank, (store, cost) in enumerate(ranked_stores, 1):
        is_complete = store_price_counts[store] == total_items
        if best_single_store and is_complete:
            diff = cost - best_single_store_cost
            diff_str = "+$0.00" if diff == 0 else f"+{format_price(diff)} more"
        else:
            diff_str = "Complete basket" if is_complete else "Partial basket"
        store_rankings.append({
            "store": store,
            "rank": rank,
            "total_cost": cost,
            "difference_from_best": diff_str,
            "coverage_count": store_price_counts[store],
            "coverage_total": total_items,
            "is_complete": is_complete,
        })

    best_available_store = ranked_stores[0][0]
    best_available_cost = store_totals[best_available_store]
    comparable_items = [
        item for item in item_breakdown if item["price_options_count"] > 1
    ]
    price_selection_savings = round(
        sum(item["savings_vs_highest"] for item in comparable_items),
        2,
    )
    
    return {
        "total_items": total_items,
        "trip_savings": trip_savings,
        "price_selection_savings": {
            "amount": price_selection_savings,
            "compared_items": len(comparable_items),
        },
        "comparison_modes": {
            "single_store_best": {
                "store_name": best_single_store or best_available_store,
                "total_cost": best_single_store_cost if best_single_store else best_available_cost,
                "is_recommended": bool(best_single_store),
                "is_complete": bool(best_single_store),
            },
            "split_store_optimal": {
                "total_cost": split_store_total,
                "description": "Buy each item where it's cheapest across your stores",
            },
        },
        "store_rankings": store_rankings,
        "item_breakdown": item_breakdown,
        "unpriced_items": unpriced_items,
    }


try:
    css_path = pathlib.Path(__file__).parent / "static" / "styles.css"
    if css_path.exists():
        css_content = css_path.read_text()
        st.markdown(f"<style>{css_content}</style>", unsafe_allow_html=True)
        logger.info("External CSS stylesheet loaded successfully")
    else:
        logger.warning(f"CSS file not found at {css_path}")
except Exception as e:
    logger.warning(f"Failed to load external CSS: {e}")

# ============================================================================
# INLINE CSS FOR DYNAMIC STORE STYLING
# ============================================================================
# Store pill colors are dynamic based on current preferences
st.markdown(f"""
<style>
    /* DYNAMIC STORE PILLS */
    div:has(> .store-pills-marker) + div[data-testid="stHorizontalBlock"] div[data-testid="stCheckbox"] {{ margin: 0 !important; }}
    div:has(> .store-pills-marker) + div[data-testid="stHorizontalBlock"] div[data-testid="stCheckbox"] label > div:first-child {{ display: none !important; }}
    div:has(> .store-pills-marker) + div[data-testid="stHorizontalBlock"] div[data-testid="stCheckbox"] label {{ display: block !important; cursor: pointer !important; }}
    div:has(> .store-pills-marker) + div[data-testid="stHorizontalBlock"] div[data-testid="stCheckbox"] label p {{ padding: 7px 9px; border-radius: 9px; font-weight: 700; font-size: 12px; display: block; margin: 0; white-space: nowrap; text-align: center; transition: background-color 0.18s ease, color 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease; }}
    div:has(> .store-pills-marker) + div[data-testid="stHorizontalBlock"] div[data-testid="column"]:nth-child(1) div[data-testid="stCheckbox"] label p {{ background-color: {'#005A36' if prefs['Woolworths'] else '#FFFFFF'}; color: {'white' if prefs['Woolworths'] else '#005A36'}; border: 1px solid #005A36; }}
    div:has(> .store-pills-marker) + div[data-testid="stHorizontalBlock"] div[data-testid="column"]:nth-child(2) div[data-testid="stCheckbox"] label p {{ background-color: {'#E31837' if prefs['Coles'] else '#FFFFFF'}; color: {'white' if prefs['Coles'] else '#E31837'}; border: 1px solid #E31837; }}
    div:has(> .store-pills-marker) + div[data-testid="stHorizontalBlock"] div[data-testid="column"]:nth-child(3) div[data-testid="stCheckbox"] label p {{ background-color: {'#002D62' if prefs['Aldi'] else '#FFFFFF'}; color: {'white' if prefs['Aldi'] else '#002D62'}; border: 1px solid #002D62; }}
</style>
""", unsafe_allow_html=True)

# =====================================================================
# --- 4. WELCOME SPLASH SCREEN ---
# =====================================================================
if not st.session_state["app_started"]:
    st.markdown(f"""
    <style>
        .stApp {{
            background-color: #005A36 !important;
        }}
        div[data-testid="stMainBlockContainer"], .main .block-container {{
            padding: 0 !important;
        }}
        header[data-testid="stHeader"] {{
            display: none !important;
        }}
    </style>
    <div style="background-color: #005A36; padding: 180px 20px 30px 20px; text-align: center; color: white; box-sizing: border-box;">
        <img class="splash-brand-mark" src="{BRAND_MARK_DATA_URI}" alt="{BRAND_NAME} gecko mark" />
        <div class="splash-brand-title">
            <h1 style="font-family: 'Georgia', serif; font-size: 36px; font-weight: 700; margin: 0; color: white;">{BRAND_NAME}</h1>
            <span class="splash-beta-badge">BETA</span>
        </div>
        <p style="font-size: 15px; opacity: 0.9; margin: 0 0 40px 0; font-weight: 400;">{BRAND_TAGLINE}</p>
    </div>
    """, unsafe_allow_html=True)
    
    col_l, col_m, col_r = st.columns([0.1, 0.8, 0.1])
    with col_m:
        if st.button("Get Started", type="primary", use_container_width=True, key="splash_get_started"):
            st.session_state["app_started"] = True
            st.query_params["welcome_seen"] = "1"
            st.rerun()

# =====================================================================
# --- 5. AUTHENTICATION & FORGOT PASSWORD ROUTING ---
# =====================================================================
elif not st.session_state["authenticated"]:
    
    # -----------------------------------------------------------
    # VIEW: LOGIN
    # -----------------------------------------------------------
    if st.session_state["auth_mode"] == "login":
        st.markdown(f"""
        <div class="auth-header">
            <div class="auth-logo"><img src="{BRAND_MARK_DATA_URI}" alt="{BRAND_NAME} gecko mark" /></div>
            <h1>Welcome back</h1>
            <p class="auth-subtitle">Sign in to pick up your shopping list.</p>
        </div>
        """, unsafe_allow_html=True)

        if st.session_state.get("auth_notice"):
            st.success(st.session_state.pop("auth_notice"))
        
        st.markdown('<div class="login-screen-marker"></div>', unsafe_allow_html=True)
        with st.form("login_form"):
            email = st.text_input("Email address", placeholder="Email address", label_visibility="collapsed")
            pwd = st.text_input("Password", type="password", placeholder="Password", label_visibility="collapsed")
            
            submitted = st.form_submit_button("Sign In", type="primary", use_container_width=True)
            if submitted:
                st.session_state["reset_email"] = email.strip()
                if not validate_email(email):
                    st.error("Please enter a valid email address.")
                elif not pwd:
                    st.error("Please enter your password.")
                else:
                    user = auth_manager.authenticate(email, pwd)
                    if user:
                        st.session_state["authenticated"] = True
                        st.session_state["current_user"] = user
                        st.query_params["auth_token"] = user["token"]
                        st.rerun()
                    else:
                        st.error("Email or password is incorrect.")
                
        st.markdown('<div class="auth-links-marker"></div>', unsafe_allow_html=True)
        
        _, center_col, _ = st.columns([1, 2, 1])
        with center_col:
            st.markdown('<div class="forgot-password-button-marker"></div>', unsafe_allow_html=True)
            if st.button("Forgot password?", use_container_width=True):
                st.session_state["auth_mode"] = "forgot_password"
                st.rerun()
            st.markdown('<div class="signup-button-marker"></div>', unsafe_allow_html=True)
            if st.button("Don't have an account? **Sign up**", use_container_width=True):
                st.session_state["auth_mode"] = "signup"
                st.rerun()
                
    # -----------------------------------------------------------
    # VIEW: SIGN UP
    # -----------------------------------------------------------
    elif st.session_state["auth_mode"] == "signup":
        st.markdown(f"""
        <div class="auth-header">
            <div class="auth-logo"><img src="{BRAND_MARK_DATA_URI}" alt="{BRAND_NAME} gecko mark" /></div>
            <h1>Create account</h1>
            <p class="auth-subtitle">Save your list. Compare prices. Keep the difference.</p>
        </div>
        """, unsafe_allow_html=True)
        
        st.markdown('<div class="signup-screen-marker"></div>', unsafe_allow_html=True)
        with st.form("signup_form"):
            first_name = st.text_input("First name", placeholder="First name", label_visibility="collapsed")
            surname = st.text_input("Surname", placeholder="Surname", label_visibility="collapsed")
            email = st.text_input("Email address", placeholder="Email address", label_visibility="collapsed")
            pwd = st.text_input("Password", type="password", placeholder="Password", label_visibility="collapsed")
            country = st.selectbox("Country", config.SUPPORTED_COUNTRIES, index=config.SUPPORTED_COUNTRIES.index(config.DEFAULT_COUNTRY), label_visibility="collapsed")
            postcode = st.text_input("Postcode", placeholder=f"{country} postcode", label_visibility="collapsed")
            st.caption(f"Enter your {country} postcode (4 digits).")
            
            submitted = st.form_submit_button("Create Account", type="primary", use_container_width=True)
            if submitted:
                if not first_name.strip():
                    st.error("Please enter your first name.")
                elif not surname.strip():
                    st.error("Please enter your surname.")
                elif not validate_email(email):
                    st.error("Please enter a valid email address.")
                elif len(pwd) < 8:
                    st.error("Password must be at least 8 characters.")
                elif not postcode.isdigit() or len(postcode) != 4:
                    st.error(f"Please enter a valid 4-digit {country} postcode.")
                else:
                    user = auth_manager.create_user(first_name, surname, email, pwd, postcode, country)
                    if user:
                        st.session_state["authenticated"] = True
                        st.session_state["current_user"] = user
                        st.query_params["auth_token"] = user["token"]
                        st.rerun()
                    else:
                        st.error("An account with that email already exists.")
                
        st.markdown('<div class="auth-links-marker signup-links-marker"></div>', unsafe_allow_html=True)
        
        _, center_col, _ = st.columns([1, 2, 1])
        with center_col:
            if st.button("Already have an account? **Sign in**", use_container_width=True):
                st.session_state["auth_mode"] = "login"
                st.rerun()
                
    # -----------------------------------------------------------
    # VIEW: FORGOT PASSWORD (STEP 1)
    # -----------------------------------------------------------
    elif st.session_state["auth_mode"] == "forgot_password":
        st.markdown("""
        <div class="auth-header">
            <div style="font-size: 32px; margin-bottom: -5px;">🔑</div>
            <h1>Reset password</h1>
        </div>
        """, unsafe_allow_html=True)
        
        st.markdown('<div id="subpage-back-anchor"></div>', unsafe_allow_html=True)
        if st.button("Back", key="btn_forgot_back"):
            st.session_state["auth_mode"] = "login"
            st.rerun()
            
        st.markdown("""
        <div style="color: #555; font-size: 14px; line-height: 1.5; margin-bottom: 20px;">
            Reset your password using the email address and postcode saved on your account.
        </div>
        """, unsafe_allow_html=True)
        
        with st.form("forgot_form"):
            reset_email_input = st.text_input(
                "Email address",
                value=st.session_state.get("reset_email", ""),
                placeholder="Email address",
                label_visibility="collapsed",
            )
            
            submitted = st.form_submit_button("Continue", type="primary", use_container_width=True)
            if submitted:
                if reset_email_input and validate_email(reset_email_input):
                    st.session_state["reset_email"] = reset_email_input.strip()
                    st.session_state["auth_mode"] = "forgot_reset"
                    st.rerun()
                st.error("Please enter a valid email address.")
                
    # -----------------------------------------------------------
    # VIEW: RESET PASSWORD (STEP 2)
    # -----------------------------------------------------------
    elif st.session_state["auth_mode"] == "forgot_reset":
        st.markdown("""
        <div class="auth-header">
            <div style="font-size: 32px; margin-bottom: -5px;">🔑</div>
            <h1>Reset password</h1>
        </div>
        """, unsafe_allow_html=True)
        
        st.markdown('<div id="subpage-back-anchor"></div>', unsafe_allow_html=True)
        if st.button("Back", key="btn_reset_back"):
            st.session_state["auth_mode"] = "login"
            st.rerun()
            
        st.markdown(f"""
        <div style="color: #555; font-size: 14px; line-height: 1.5; margin: 20px 0 20px 0;">
            Enter the postcode on your account, then choose a new password.
        </div>
        """, unsafe_allow_html=True)

        with st.form("reset_password_form"):
            reset_postcode = st.text_input("Postcode", placeholder="Postcode", label_visibility="collapsed")
            new_password = st.text_input("New password", type="password", placeholder="New password (8+ characters)", label_visibility="collapsed")
            confirm_password = st.text_input("Confirm password", type="password", placeholder="Confirm new password", label_visibility="collapsed")

            submitted = st.form_submit_button("Reset Password", type="primary", use_container_width=True)
            if submitted:
                target_email = st.session_state.get("reset_email", "")
                if not reset_postcode.isdigit() or len(reset_postcode) != 4:
                    st.error("Please enter your 4-digit postcode.")
                elif len(new_password) < 8:
                    st.error("Password must be at least 8 characters.")
                elif new_password != confirm_password:
                    st.error("The passwords do not match.")
                elif auth_manager.reset_password(target_email, reset_postcode, new_password):
                    st.session_state["auth_mode"] = "login"
                    st.session_state["reset_email"] = target_email
                    st.session_state["auth_notice"] = "Your password has been reset. You can now sign in."
                    st.rerun()
                else:
                    st.error(
                        f"We could not verify {target_email or 'that email'} and postcode combination. "
                        "Please go back and confirm the email address on your account."
                    )

# =====================================================================
# --- 6. MAIN APP (Authenticated User) ---
# =====================================================================
else:
    # -----------------------------------------------------------
    # VIEW: CUSTOMER PROFILE
    # -----------------------------------------------------------
    if st.session_state["current_page"] == "profile":
        current_user = st.session_state.get("current_user") or {}
        first_name = current_user.get("first_name", "")
        surname = current_user.get("surname", "")
        full_name = " ".join(part for part in (first_name, surname) if part).strip()
        if not full_name:
            full_name = current_user.get("name", "Grocery Gecko shopper")
        email = current_user.get("email", "")
        postcode = current_user.get("postcode", "")
        country = current_user.get("country", config.DEFAULT_COUNTRY)
        initials = html.escape("".join(part[0] for part in full_name.split()[:2] if part).upper() or "GG")
        active_stores = [store for store in config.STORE_NAMES if st.session_state["prefs"].get(store)]

        st.markdown(f"""
        <div class="profile-hero">
            <div class="profile-hero-copy">
                <span class="profile-eyebrow">YOUR GROCERY GECKO</span>
                <h1>Profile</h1>
                <p>Your account, preferences and support in one place.</p>
            </div>
            <img src="{BRAND_MARK_DATA_URI}" alt="{BRAND_NAME} gecko mark" />
        </div>
        """, unsafe_allow_html=True)

        st.markdown('<div class="profile-done-marker"></div>', unsafe_allow_html=True)
        if st.button("Done", key="profile_done"):
            st.session_state["current_page"] = "home"
            st.rerun()

        safe_name = html.escape(full_name)
        safe_email = html.escape(email)
        safe_location = html.escape(" · ".join(part for part in (postcode, country) if part))
        store_chips = "".join(
            f'<span class="profile-store-chip"><i style="background:{config.STORES[store]["color"]}"></i>{html.escape(store)}</span>'
            for store in active_stores
        ) or '<span class="profile-store-empty">No preferred stores selected</span>'
        st.markdown(f"""
        <section class="profile-account-card">
            <div class="profile-avatar">{initials}</div>
            <div class="profile-account-copy">
                <h2>{safe_name}</h2>
                <p>{safe_email}</p>
                <span>{safe_location}</span>
            </div>
        </section>
        <section class="profile-preferences">
            <div>
                <span class="profile-section-kicker">SHOPPING PREFERENCES</span>
                <h2>Your comparison stores</h2>
            </div>
            <div class="profile-store-chips">{store_chips}</div>
            <p>Change these any time from the preferred stores section on your home screen.</p>
        </section>
        """, unsafe_allow_html=True)

        st.markdown('<p class="profile-group-label">HELP &amp; SHARING</p>', unsafe_allow_html=True)
        profile_actions = [
            ("Support & feedback", "Tell us about a price, product or app issue", "contact", "profile_support"),
            ("Refer a friend", "Help someone else compare their weekly shop", "refer", "profile_refer"),
            ("Privacy policy", "How we collect, use and protect your information", "privacy", "profile_privacy"),
            (f"About {BRAND_NAME}", "Our purpose and independent comparison approach", "about", "profile_about"),
        ]
        for title, description, destination, key in profile_actions:
            st.markdown(
                f'<div class="profile-action-description"><strong>{html.escape(title)}</strong><span>{html.escape(description)}</span></div>',
                unsafe_allow_html=True,
            )
            st.markdown('<div class="profile-action-marker"></div>', unsafe_allow_html=True)
            if st.button(f"Open {title}", key=key, use_container_width=True):
                if destination == "contact":
                    sheets_manager.log_user_event(email, "contact_click")
                elif destination == "refer":
                    sheets_manager.log_user_event(email, "refer_click")
                st.session_state["current_page"] = destination
                st.rerun()

        st.markdown(f"""
        <div class="profile-version">
            <span>{BRAND_NAME}</span>
            <strong>{APP_VERSION}</strong>
        </div>
        <a class="profile-signout" href="?logout=1&amp;auth_token={st.query_params.get('auth_token', '')}">Sign out</a>
        """, unsafe_allow_html=True)

    # -----------------------------------------------------------
    # VIEW: SHOP CELEBRATION (POST-FINISH)
    # -----------------------------------------------------------
    elif st.session_state["current_page"] == "celebration":
        st.markdown("""
        <div style="background-color: #005A36; color: white; padding: 60px 20px 40px 20px; margin: -60px -20px 20px -20px; border-radius: 0 0 30px 30px; text-align: center;">
            <div style="font-size: 50px; margin-bottom: 10px;">🎉</div>
            <h1 style="margin: 0; color: white; font-size: 28px; font-weight: 800;">Shop Complete!</h1>
            <p style="margin: 10px 0 0 0; font-size: 16px; opacity: 0.9;">Your list is saved in history for next week.</p>
        </div>
        """, unsafe_allow_html=True)
        
        savings = st.session_state.get("last_savings", 0.0)
        
        st.markdown(f"""
        <div style="text-align: center; margin: 40px 0;">
            <div style="font-size: 14px; color: #666; font-weight: bold; text-transform: uppercase;">Total Saved This Week</div>
            <div style="font-size: 48px; font-weight: 900; color: #005A36;">${savings:.2f}</div>
        </div>
        """, unsafe_allow_html=True)
        
        if st.button("💌 Refer a Friend", type="primary", use_container_width=True):
            sheets_manager.log_user_event(st.session_state["current_user"]["email"], "refer_click")
            st.session_state["current_page"] = "refer"
            st.rerun()
            
        if st.button("Back to Home", use_container_width=True):
            st.session_state["current_page"] = "home"
            st.rerun()
            
    # -----------------------------------------------------------
    # VIEW: REFER A FRIEND
    # -----------------------------------------------------------
    elif st.session_state["current_page"] == "refer":
        st.markdown("""
        <div style="background-color: #005A36; color: white; padding: 30px 20px 20px 20px; margin: -60px -20px 20px -20px; border-radius: 0 0 20px 20px; display: flex; align-items: center; gap: 15px;">
            <div style="font-size: 20px;">←</div>
            <div>
                <h1 style="margin: 0; color: white; font-size: 22px; font-weight: 800;">Refer a Friend</h1>
                <p style="margin: 0; font-size: 13px; opacity: 0.9;">Share the savings</p>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        st.markdown('<div id="subpage-back-anchor"></div>', unsafe_allow_html=True)
        if st.button("Back", key="btn_refer_back"):
            st.session_state["current_page"] = "home"
            st.rerun()
            
        st.markdown("""
        <div style="color: #444; font-size: 14px; line-height: 1.6; margin: 15px 0 25px 0;">
            Know someone who could use a hand beating supermarket prices? Send them a quick invite to try Grocery Gecko.
        </div>
        """, unsafe_allow_html=True)
        
        savings = st.session_state.get("last_savings", 0.0)
        
        # Dynamically build the message body using the saved trip amount and dynamic signature 
        if savings > 0:
            default_msg = f"Hey you should give this a try. It saved me ${savings:.2f} on this week's shop.\n\nCheers,\nBrad\n\nTry Grocery Gecko:"
        else:
            default_msg = "Hey, you should give this a try. It helps me compare grocery prices before I shop.\n\nCheers,\nBrad\n\nTry Grocery Gecko:"
            
        with st.form("refer_form"):
            recipient = st.text_input("Friend's Email Address", placeholder="e.g. friend@example.com")
            msg = st.text_area("Message Preview", value=default_msg, height=140)
            
            st.markdown("""
            <div style="display:flex; gap:10px; margin-bottom: 25px; justify-content: center;">
                <img src="https://upload.wikimedia.org/wikipedia/commons/3/3c/Download_on_the_App_Store_Badge.svg" width="120" style="cursor: pointer;">
                <img src="https://upload.wikimedia.org/wikipedia/commons/7/78/Google_Play_Store_badge_EN.svg" width="120" style="cursor: pointer;">
            </div>
            """, unsafe_allow_html=True)
            
            submit = st.form_submit_button("Send Invitation", type="primary", use_container_width=True)
            if submit:
                if recipient:
                    st.success(f"Awesome! Invitation sent to {recipient}.")
                else:
                    st.error("Please enter an email address.")
                    
    # -----------------------------------------------------------
    # VIEW: ABOUT US PAGE
    # -----------------------------------------------------------
    elif st.session_state["current_page"] == "about":
        st.markdown("""
        <div style="background-color: #005A36; color: white; padding: 30px 20px 20px 20px; margin: -60px -20px 20px -20px; border-radius: 0 0 20px 20px; display: flex; align-items: center; gap: 15px;">
            <div style="font-size: 20px;">←</div>
            <div>
                <h1 style="margin: 0; color: white; font-size: 22px; font-weight: 800;">About Us</h1>
                <p style="margin: 0; font-size: 13px; opacity: 0.9;">Grocery Gecko Information</p>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        st.markdown('<div id="subpage-back-anchor"></div>', unsafe_allow_html=True)
        if st.button("Back", key="btn_about_back"):
            st.session_state["current_page"] = "home"
            st.rerun()
            
        st.markdown("""
        <div style="color: #444; font-size: 14px; line-height: 1.7; margin-top: 10px;">
            <h3 style="color: #005A36; font-size: 18px; margin-bottom: 10px;">Welcome to Grocery Gecko</h3>
            <p>Grocery Gecko is Australia's independent grocery price comparison companion, designed to help households cut through supermarket price hikes and make informed shopping choices.</p>
            
            <h4 style="color: #222; font-size: 15px; margin-top: 20px;">Our Mission</h4>
            <p>We believe grocery shopping shouldn't require visiting multiple stores blindly or sorting through confusing catalogues. By tracking pricing across major Australian supermarkets like Woolworths, Coles, and Aldi, Grocery Gecko shows you whether you save more by buying your whole basket at one store or splitting your items across the cheapest options.</p>
            
            <h4 style="color: #222; font-size: 15px; margin-top: 20px;">Built for Everyday Australians</h4>
            <p>Created to simplify weekly budgeting, Grocery Gecko puts clear price comparisons back into your hands. No corporate bias, just available pricing data from your preferred stores.</p>
        </div>
        """, unsafe_allow_html=True)
        
    # -----------------------------------------------------------
    # VIEW: PRIVACY POLICY PAGE
    # -----------------------------------------------------------
    elif st.session_state["current_page"] == "privacy":
        st.markdown("""
        <div style="background-color: #005A36; color: white; padding: 30px 20px 20px 20px; margin: -60px -20px 20px -20px; border-radius: 0 0 20px 20px; display: flex; align-items: center; gap: 15px;">
            <div style="font-size: 20px;">←</div>
            <div>
                <h1 style="margin: 0; color: white; font-size: 22px; font-weight: 800;">Privacy Policy</h1>
                <p style="margin: 0; font-size: 13px; opacity: 0.9;">Data Protection & Terms</p>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        st.markdown('<div id="subpage-back-anchor"></div>', unsafe_allow_html=True)
        if st.button("Back", key="btn_privacy_back"):
            st.session_state["current_page"] = "home"
            st.rerun()
            
        st.markdown("""
        <div style="color: #444; font-size: 14px; line-height: 1.7; margin-top: 10px;">
            <h3 style="color: #005A36; font-size: 18px; margin-bottom: 10px;">Privacy Policy & Data Protection</h3>
            <p><i>Last updated: August 2026</i></p>
            
            <p>Grocery Gecko respects your privacy and is committed to protecting any personal data you share with us. This policy outlines how your information is handled.</p>
            
            <h4 style="color: #222; font-size: 15px; margin-top: 20px;">1. Information We Collect</h4>
            <p>When you create an account or use Grocery Gecko, we collect your name, email address, postal location (postcode), store preferences, custom shopping lists, and images from barcode scans that could not be decoded.</p>
            
            <h4 style="color: #222; font-size: 15px; margin-top: 20px;">2. How We Use Your Data</h4>
            <p>Your data is used solely to provide and improve your app experience, such as saving your preferred shopping lists and configuring store comparisons. Failed barcode images are linked to your account and retained privately for scan-quality analysis; successful barcode captures are not retained. Feedback or problem reports submitted through the app are securely routed directly to our administrative team.</p>
            
            <h4 style="color: #222; font-size: 15px; margin-top: 20px;">3. Data Security</h4>
            <p>We implement secure authentication standards and encrypted database connections to ensure your personal information remains confidential and protected against unauthorized access.</p>
            
            <h4 style="color: #222; font-size: 15px; margin-top: 20px;">4. Contact Us</h4>
            <p>If you have any questions regarding this privacy policy or how your data is managed, please reach out to us via the <b>Spot a Problem / Contact Us</b> section in the app footer.</p>
        </div>
        """, unsafe_allow_html=True)
        
    # -----------------------------------------------------------
    # VIEW: CONTACT / SPOT A PROBLEM PAGE
    # -----------------------------------------------------------
    elif st.session_state["current_page"] == "contact":
        st.markdown("""
        <div style="background-color: #005A36; color: white; padding: 30px 20px 20px 20px; margin: -60px -20px 20px -20px; border-radius: 0 0 20px 20px; display: flex; align-items: center; gap: 15px;">
            <div style="font-size: 20px;">←</div>
            <div>
                <h1 style="margin: 0; color: white; font-size: 22px; font-weight: 800;">Spot a Problem</h1>
                <p style="margin: 0; font-size: 13px; opacity: 0.9;">Contact & Support</p>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        st.markdown('<div id="subpage-back-anchor"></div>', unsafe_allow_html=True)
        if st.button("Back", key="btn_contact_back"):
            st.session_state["current_page"] = "home"
            st.rerun()
            
        st.markdown("""
        <div style="border-left: 3px solid #005A36; padding-left: 15px; margin-bottom: 30px; margin-top: 10px; color: #444; font-size: 14px; line-height: 1.6;">
            We're not perfect — <b>and that's okay.</b> Prices change, items get miscategorised, and occasionally things just don't work the way they should. Your reports are what help us fix it.<br><br>
            Tell us what you found and we'll get straight onto it.
        </div>
        """, unsafe_allow_html=True)
        
        with st.form("contact_form"):
            st.markdown("<b style='font-size: 13px;'>Your email address</b>", unsafe_allow_html=True)
            email_input = st.text_input("Email", placeholder="so we can follow up when it's fixed", label_visibility="collapsed")
            
            st.markdown("<br><b style='font-size: 13px;'>What did you spot?</b>", unsafe_allow_html=True)
            feedback_input = st.text_area("Feedback", placeholder="Describe the problem in as much detail as you can. The more context you give us, the faster we can fix it.", height=150, label_visibility="collapsed")
            
            st.markdown("<br><b style='font-size: 13px;'>Attach a screenshot (optional)</b>", unsafe_allow_html=True)
            screenshot_input = st.file_uploader(
                "Screenshot",
                type=["png", "jpg", "jpeg", "gif", "webp"],
                label_visibility="collapsed",
            )
            
            submitted = st.form_submit_button("Submit Feedback", type="primary", use_container_width=True)
            
            if submitted:
                if not email_input or not feedback_input:
                    st.error("Please fill in both fields so we can assist you properly.")
                else:
                    success = FeedbackManager.send_feedback(email_input, feedback_input, screenshot_input)
                    if success:
                        st.success("Thanks! Your feedback has been sent to our team.")
                    else:
                        st.error("Something went wrong sending the report. Please try again later.")
                        
    # -----------------------------------------------------------
    # VIEW: HOME / LIST PAGE
    # -----------------------------------------------------------
    elif st.session_state["current_page"] == "home":
        current_user = st.session_state.get("current_user") or {}
        display_name = current_user.get("first_name") or current_user.get("name", "there").split()[0]
        user_id = current_user.get("email", "")
        greeting = get_greeting(current_user.get("country", config.DEFAULT_COUNTRY))

        if st.session_state.pop("clear_add_item_description", False):
            st.session_state["add_item_description"] = ""
            
        st.markdown(f"""
        <div class="app-header">
            <div>
                <p>{greeting}</p>
                <h1>{display_name}</h1>
            </div>
            <a class="header-logout-link header-profile-link" href="?profile=1&amp;auth_token={st.query_params.get('auth_token', '')}" title="Open profile" aria-label="Open profile"><img src="{BRAND_MARK_DATA_URI}" alt="" /></a>
        </div>
        """, unsafe_allow_html=True)
        
        with st.container(border=True):
            st.markdown('<p class="add-item-heading">ADD ITEM</p>', unsafe_allow_html=True)
            st.markdown('<div class="add-item-form-marker"></div>', unsafe_allow_html=True)
            with st.form("add_item_form", clear_on_submit=False):
                item_name = st.text_input(
                    "What do you need?",
                    placeholder="e.g., Helga's white bread 700g",
                    label_visibility="collapsed",
                    key="add_item_description",
                )
                c1, c2, c3 = st.columns([1.2, 2, 1.4])
                with c1:
                    qty = st.number_input("Qty", min_value=1, value=1, label_visibility="collapsed")
                with c2:
                    unit = st.selectbox("Unit", config.UNIT_OPTIONS, label_visibility="collapsed")
                with c3:
                    add_submitted = st.form_submit_button("Add directly", use_container_width=True)
                find_submitted = st.form_submit_button("🔍 Find matches", use_container_width=True)
                
                if add_submitted and item_name:
                    stored_qty = int(qty)
                    stored_unit = config.UNIT_VALUES[unit]
                    if stored_unit == "auto":
                        stored_qty, stored_unit = infer_quantity_and_unit(item_name)
                    scraped_matches = sheets_manager.search_scraped_products(item_name)
                    scraped_product = next(
                        (match for match in scraped_matches if match.get("image_url")),
                        None,
                    )
                    product_image = scraped_product["image_url"] if scraped_product else ""
                    sheets_manager.save_product(
                        user_id,
                        item_name,
                        product_image,
                        category=scraped_product.get("category", "") if scraped_product else "",
                        subcategory=scraped_product.get("subcategory", "") if scraped_product else "",
                    )
                    if sheets_manager.add_item_to_list(
                        item_name,
                        stored_qty,
                        stored_unit,
                        product_image,
                        user_id,
                    ):
                        sheets_manager.log_user_event(user_id, "item_added", mode="direct")
                        st.session_state["search_performed"] = False
                        st.session_state["search_results"] = []
                        st.rerun()
                    else:
                        st.error("Failed to add item. Please try again.")
                elif add_submitted:
                    st.warning("Enter a product description first.")

                if find_submitted:
                    st.session_state["search_performed"] = True
                    if item_name.strip():
                        with st.spinner("Finding the closest product matches..."):
                            search_query = build_product_search_query(item_name, qty, unit)
                            local_results = sheets_manager.search_saved_products(
                                user_id,
                                search_query,
                                limit=None,
                            )
                            scraped_results = sheets_manager.search_scraped_products(
                                search_query,
                                limit=None,
                            )
                            seen_titles = {
                                result["title"].strip().lower()
                                for result in local_results
                            }
                            unique_scraped_results = [
                                result
                                for result in scraped_results
                                if result["title"].strip().lower() not in seen_titles
                            ]
                            st.session_state["search_results"] = (
                                local_results + unique_scraped_results
                            )
                    else:
                        st.session_state["search_results"] = []
                        st.warning("Enter a product description first.")
            
            # Dynamic label toggle hack to force the expander to reset/collapse upon state change
            recent_items = []
            if not st.session_state.get("recent_shops_available", False):
                recent_items = sheets_manager.get_recent_history(user_id)
                st.session_state["recent_shops_available"] = bool(recent_items)
            recent_marker = (
                "recent-shops-visible-marker"
                if st.session_state["recent_shops_available"]
                else "recent-shops-hidden-marker"
            )
            st.markdown(f'<div class="{recent_marker}"></div>', unsafe_allow_html=True)
            recent_shops_label = "🕒 Add from Recent Shops" + ("\u200B" if st.session_state["expander_toggle"] else "")
            with st.expander(recent_shops_label):
                if not recent_items:
                    recent_items = sheets_manager.get_recent_history(
                        st.session_state["current_user"]["email"]
                    )
                if not recent_items:
                    st.info("No shopping history found for the last 3 weeks. Once you run a price comparison and click 'Finish Shop', your items will be saved here!")
                else:
                    with st.form("recent_shops_form", clear_on_submit=True):
                        st.markdown("<div style='font-size: 13px; font-weight:600; color:#555; margin-bottom: 10px;'>Select items to re-add to your list:</div>", unsafe_allow_html=True)
                        selected_indices = []
                        for idx, r_item in enumerate(recent_items):
                            cols = st.columns([0.15, 0.15, 0.7])
                            with cols[0]:
                                chk = st.checkbox("Select Item", key=f"rec_chk_{idx}", label_visibility="collapsed")
                                if chk:
                                    selected_indices.append(idx)
                            with cols[1]:
                                if r_item["img"]:
                                    st.markdown(f'<img src="{r_item["img"]}" class="thumbnail-zoom" style="width:28px; height:28px; margin-top:-5px;" />', unsafe_allow_html=True)
                                else:
                                    st.markdown(f'<div style="background-color: #E6F4EA; width: 28px; height: 28px; border-radius: 6px; display: flex; justify-content: center; align-items: center; margin-top:-5px;"><img src="{BRAND_MARK_DATA_URI}" alt="" style="width:24px; height:24px;" /></div>', unsafe_allow_html=True)
                            with cols[2]:
                                st.markdown(f'<div style="font-size: 14px; font-weight: 500; margin-top:-2px;">{r_item["name"]}</div>', unsafe_allow_html=True)
                            
                            st.markdown("<div style='height: 4px;'></div>", unsafe_allow_html=True)
                        
                        if st.form_submit_button("➕ Add Selected to List", use_container_width=True):
                            if selected_indices:
                                for i in selected_indices:
                                    sr = recent_items[i]
                                    sheets_manager.save_product(user_id, sr["name"], sr["img"])
                                    sheets_manager.add_item_to_list(
                                        sr["name"],
                                        int(sr["qty"]),
                                        sr["unit"],
                                        sr["img"],
                                        user_id,
                                        shopping_pack_count(
                                            int(sr["qty"]),
                                            sr["unit"],
                                            sr.get("pack_count"),
                                        ),
                                    )
                                    sheets_manager.log_user_event(user_id, "item_added", mode="recent_shops")
                                
                                # Toggle state to trick Streamlit into generating a "new" expander component that starts collapsed
                                st.session_state["expander_toggle"] = not st.session_state["expander_toggle"]
                                st.session_state["search_performed"] = False
                                st.session_state["search_results"] = []
                                st.rerun()
                                
            if st.session_state.get("search_performed", False):
                st.markdown("<div class='matching-products-heading'>Matching products</div>", unsafe_allow_html=True)
                if st.session_state.get("search_results"):
                    st.markdown("<hr style='margin: 10px 0; opacity: 0.2;'>", unsafe_allow_html=True)
                    for idx, res in enumerate(st.session_state["search_results"]):
                        sc1, sc2, sc3 = st.columns([1, 3, 1.5])
                        with sc1:
                            if res["image_url"]:
                                st.markdown(
                                    f'<img src="{res["image_url"]}" alt="{res["title"]}" class="product-result-image" />',
                                    unsafe_allow_html=True,
                                )
                            else:
                                st.markdown(
                                    f'<div class="product-result-placeholder"><img src="{BRAND_MARK_DATA_URI}" alt="" /></div>',
                                    unsafe_allow_html=True,
                                )
                        with sc2:
                            st.markdown(f"<div style='font-size: 13px; font-weight: 600; line-height: 1.2; padding-top: 5px;'>{res['title']}</div>", unsafe_allow_html=True)
                            metadata = []
                            if res.get("category"):
                                metadata.append(res["category"])
                            if res.get("subcategory"):
                                metadata.append(res["subcategory"])
                            if res.get("stores"):
                                metadata.append(", ".join(res["stores"]))
                            if metadata:
                                st.markdown(
                                    f"<div style='font-size: 11px; color: #666; margin-top: 4px; line-height: 1.4;'>{' · '.join(metadata)}</div>",
                                    unsafe_allow_html=True,
                                )
                        with sc3:
                            if st.button("➕ Add", key=f"add_search_{idx}", use_container_width=True):
                                matched_qty, matched_unit = infer_quantity_and_unit(res["title"])
                                sheets_manager.save_product(
                                    user_id,
                                    res["title"],
                                    res["image_url"],
                                    category=res.get("category", ""),
                                    subcategory=res.get("subcategory", ""),
                                )
                                if sheets_manager.add_item_to_list(
                                    res["title"], matched_qty, matched_unit, res["image_url"], user_id
                                ):
                                    sheets_manager.log_user_event(user_id, "item_added", mode="find_matches")
                                    st.session_state["clear_add_item_description"] = True
                                    st.session_state["search_performed"] = False
                                    st.session_state["search_results"] = []
                                    st.rerun()
                                else:
                                    st.error("Failed to add item. Please try again.")
                        st.markdown("<div style='height: 5px;'></div>", unsafe_allow_html=True)
                else:
                    st.info("No products found. Try adding a brand or pack size.")
                        
            with st.expander("📷 Scan Barcode from Pantry"):
                st.caption(
                    "If a barcode cannot be decoded, the captured image is saved privately "
                    "to your profile for scan-quality analysis."
                )
                camera_context = webrtc_streamer(
                    key="barcode_camera",
                    mode=WebRtcMode.SENDONLY,
                    media_stream_constraints={
                        "video": {
                            "facingMode": {"ideal": "environment"},
                            "width": {"ideal": 1280},
                            "height": {"ideal": 720},
                        },
                        "audio": False,
                    },
                    video_html_attrs={
                        "autoPlay": True,
                        "controls": False,
                        "muted": True,
                    },
                    async_processing=True,
                )
                if camera_context.state.playing and st.button(
                    "Capture barcode",
                    type="primary",
                    use_container_width=True,
                ):
                    try:
                        frames = camera_context.video_receiver.get_frames(timeout=2)
                        captured_image = Image.fromarray(frames[-1].to_ndarray(format="rgb24"))
                    except Exception as error:
                        logger.warning("Could not capture barcode camera frame: %s", error)
                        st.error("The camera frame could not be captured. Please try again.")
                        captured_image = None

                    if captured_image is not None:
                        with st.spinner("Reading barcode..."):
                            barcode_number = BarcodeScanner._try_all_decode_passes(captured_image)
                        if barcode_number:
                            st.success(f"Scanned Barcode: `{barcode_number}`")
                            scanned_product = resolve_scanned_product(
                                sheets_manager,
                                barcode_number,
                            )
                            if scanned_product:
                                product_name = scanned_product["title"]
                                product_image = scanned_product.get("image_url", "")
                                st.info(f"Found: **{product_name}**")
                                if scanned_product.get("stores"):
                                    st.caption(f"Available in catalogue: {', '.join(scanned_product['stores'])}")
                                if product_image:
                                    st.image(product_image, width=100)
                                if st.button(f"➕ Add '{product_name}' to List", type="primary"):
                                    scanned_qty, scanned_unit = infer_quantity_and_unit(product_name)
                                    sheets_manager.save_product(
                                        user_id,
                                        product_name,
                                        product_image or "",
                                        category=scanned_product.get("category", ""),
                                        subcategory=scanned_product.get("subcategory", ""),
                                    )
                                    if sheets_manager.add_item_to_list(
                                        product_name, scanned_qty, scanned_unit, product_image or "", user_id
                                    ):
                                        sheets_manager.log_user_event(user_id, "item_added", mode="barcode")
                                        st.success(f"Added {product_name} to your list!")
                                        st.session_state["search_performed"] = False
                                        st.session_state["search_results"] = []
                                        st.rerun()
                                    else:
                                        st.error("Failed to add item. Please try again.")
                            else:
                                st.warning("Product not found in database. Please enter the name manually.")
                        else:
                            archived = False
                            try:
                                image_bytes = encode_failed_scan(captured_image)
                                drive_file_id = failed_scan_store.upload(image_bytes)
                                archived = sheets_manager.log_failed_barcode_scan(
                                    user_id,
                                    drive_file_id,
                                    len(image_bytes),
                                )
                            except Exception as error:
                                logger.error("Failed to archive barcode capture: %s", error, exc_info=True)
                            if archived:
                                st.warning(
                                    "No barcode was detected. The captured image was saved privately "
                                    "to your profile for later analysis."
                                )
                            else:
                                st.error(
                                    "No barcode was detected, and the image could not be saved. "
                                    "Please try again."
                                )
                            
        st.markdown('<p class="preferred-stores-heading">PREFERRED STORES</p>', unsafe_allow_html=True)
        st.markdown('<div class="store-pills-marker"></div>', unsafe_allow_html=True)
        
        col1, col2, col3 = st.columns(3, gap="small")
        store_columns = [
            (col1, "Woolworths", "woolworths"),
            (col2, "Coles", "coles"),
            (col3, "Aldi", "aldi"),
        ]
        for store_column, store_name, store_key in store_columns:
            with store_column:
                st.markdown(f'<div class="{store_key}-store-button-marker"></div>', unsafe_allow_html=True)
                if st.button(
                    store_name,
                    key=f"store_toggle_{store_key}",
                    type="primary" if prefs[store_name] else "secondary",
                    use_container_width=True,
                ):
                    updated_prefs = prefs.copy()
                    updated_prefs[store_name] = not prefs[store_name]
                    if sheets_manager.save_store_preferences(updated_prefs):
                        st.session_state["prefs"] = updated_prefs
                    st.rerun()

        new_prefs = st.session_state["prefs"]
            
        active_names = [name for name, active in new_prefs.items() if active]
        st.markdown(
            f'<p class="preferred-stores-caption">✓ We\'ll highlight {", ".join(active_names)} in the comparison</p>',
            unsafe_allow_html=True,
        )

        try:
            current_items = sheets_manager.get_shopping_list(user_id)
        except Exception as e:
            logger.error(f"Error retrieving shopping list: {e}")
            current_items = []
            
        valid_rows_with_indices = []
        if current_items:
            for sheet_idx, row in enumerate(current_items, start=1):
                if len(row) >= 3 and row[0].strip():
                    valid_rows_with_indices.append((sheet_idx, row))
                    
        item_count = len(valid_rows_with_indices)
        c_head1, c_head2 = st.columns([3, 1], vertical_alignment="center")
        with c_head1:
            st.markdown(
                f'<p class="shopping-list-heading">MY LIST ({item_count} ITEMS)</p>',
                unsafe_allow_html=True,
            )
        with c_head2:
            if item_count > 0:
                st.markdown("<div style='text-align: right;'>", unsafe_allow_html=True)
                st.markdown('<div class="clear-all-marker"></div>', unsafe_allow_html=True)
                if st.button("Clear all", type="secondary"):
                    if sheets_manager.clear_shopping_list(user_id):
                        st.rerun()
                    else:
                        st.error("Failed to clear shopping list. Please try again.")
                st.markdown("</div>", unsafe_allow_html=True)
                
        if valid_rows_with_indices:
            for sheet_idx, row in valid_rows_with_indices:
                i_name = row[0]
                try: i_qty = int(row[1])
                except ValueError: i_qty = 1
                i_unit = row[2]
                i_img = row[3].strip() if len(row) >= 4 and row[3] else ""
                i_pack_count = shopping_pack_count(
                    i_qty,
                    i_unit,
                    row[4] if len(row) >= 5 else None,
                )
                i_quantity_label = shopping_quantity_label(
                    i_qty,
                    i_unit,
                    row[4] if len(row) >= 5 else None,
                )
                st.markdown('<div class="shopping-list-item-marker"></div>', unsafe_allow_html=True)
                cols = st.columns([0.8, 2.2, 1.65])
                with cols[0]:
                    if i_img:
                        st.markdown(f'<img src="{i_img}" class="thumbnail-zoom" style="margin-top: 2px;" />', unsafe_allow_html=True)
                    else:
                        st.markdown(f'<div style="background-color: #E6F4EA; width: 80px; height: 80px; border-radius: 8px; display: flex; justify-content: center; align-items: center; margin-top: 2px;"><img src="{BRAND_MARK_DATA_URI}" alt="" style="width:60px; height:60px;" /></div>', unsafe_allow_html=True)
                with cols[1]:
                    st.markdown(f'<div style="padding-top: 2px;"><b>{i_name}</b><br><span style="color:#888; font-size:0.85em;">{i_quantity_label}</span></div>', unsafe_allow_html=True)
                with cols[2]:
                    sub_c1, sub_c2, sub_c3 = st.columns([1, 1, 1], gap=None)
                    with sub_c1:
                        st.markdown('<div class="qty-minus-marker"></div>', unsafe_allow_html=True)
                        if st.button("−", key=f"sub_{sheet_idx}"):
                            if i_pack_count > 1:
                                sheets_manager.update_list_quantity(sheet_idx, i_pack_count - 1, user_id)
                                st.rerun()
                    with sub_c2:
                        st.markdown(f'<div class="qty-value">{i_pack_count}</div>', unsafe_allow_html=True)
                    with sub_c3:
                        st.markdown('<div class="qty-plus-marker"></div>', unsafe_allow_html=True)
                        if st.button("+", key=f"add_{sheet_idx}"):
                            sheets_manager.update_list_quantity(sheet_idx, i_pack_count + 1, user_id)
                            st.rerun()
                        st.markdown('<div class="qty-del-marker"></div>', unsafe_allow_html=True)
                        if st.button("✕", key=f"del_{sheet_idx}"):
                            if sheets_manager.delete_list_row(sheet_idx, user_id):
                                st.rerun()
                            else:
                                st.error("Failed to delete item. Please try again.")
                        
                st.markdown("<hr style='margin: 10px 0; opacity: 0.2;'>", unsafe_allow_html=True)
            
            store_count_label = len(active_names)
            
            if st.button(f"🔍 Compare Prices at {store_count_label} Stores", type="primary", use_container_width=True):
                if not active_names:
                    st.error("Please select at least one store to compare.")
                else:
                    with st.spinner("Matching your list against the local price catalogue..."):
                        report = generate_smart_basket_report(current_items, active_names)
                        
                    if report:
                        sheets_manager.log_user_event(
                            user_id, "comparison_run", items_total=report["total_items"]
                        )
                        st.session_state["report"] = report
                        st.session_state["shopping_active"] = True
                        st.session_state["shop_mode"] = "overview"
                        st.rerun()
                    else:
                        diagnostics = st.session_state.pop("comparison_diagnostics", [])
                        if diagnostics:
                            formatted_rows = []
                            store_reason_map = {}
                            for detail in diagnostics:
                                store = detail.get("store", "Unknown")
                                message = detail.get("message", "Price unavailable")
                                item_name = detail.get("item", "item")
                                store_reason_map.setdefault(store, []).append(f"{item_name}: {message}")

                            for store, reasons in sorted(store_reason_map.items()):
                                unique_reasons = []
                                seen = set()
                                for reason in reasons:
                                    if reason not in seen:
                                        unique_reasons.append(reason)
                                        seen.add(reason)
                                formatted_rows.append(f"{store}: {'; '.join(unique_reasons[:2])}{' ...' if len(unique_reasons) > 2 else ''}")

                            affected_stores = ", ".join(sorted(store_reason_map.keys()))
                            summary_text = (
                                "Live supermarket checks did not return usable prices. "
                                "This is usually caused by a retailer timeout or a no-match result for the item being searched."
                            )
                            if formatted_rows:
                                summary_text += " <br><br><strong>Stores affected:</strong> " + " | ".join(formatted_rows)

                            st.markdown(
                                "<div style='background: #FDECEC; border: 1px solid #E7B0B0; border-radius: 12px; padding: 18px 18px 16px 18px; margin-bottom: 18px;'>"
                                "<div style='font-size: 18px; font-weight: 800; color: #7F1D1D; margin-bottom: 8px;'>Comparison unavailable</div>"
                                "<div style='font-size: 15px; color: #3A1B1B; line-height: 1.5;'>"
                                + summary_text
                                + "</div></div>",
                                unsafe_allow_html=True,
                            )

                            store_health = summarize_store_health(diagnostics)
                            if store_health:
                                st.markdown("#### Store health")
                                sorted_health = sorted(store_health.items())
                                for row_start in range(0, len(sorted_health), 2):
                                    summary_cols = st.columns(2, gap="small")
                                    for column, (store, stats) in zip(summary_cols, sorted_health[row_start:row_start + 2]):
                                        failed_total = stats["timeout"] + stats["not_found"] + stats["other"]
                                        if failed_total == 0:
                                            badge_text = "Healthy"
                                            badge_bg = "#E8F7EE"
                                            badge_color = "#0C6B43"
                                            card_bg = "#F7FBF8"
                                        elif stats["timeout"] > 0 and failed_total >= stats["total"] * 0.5:
                                            badge_text = "Degraded"
                                            badge_bg = "#FFF2D9"
                                            badge_color = "#9A5A00"
                                            card_bg = "#FFF9F2"
                                        else:
                                            badge_text = "Unavailable"
                                            badge_bg = "#FDECEC"
                                            badge_color = "#B42318"
                                            card_bg = "#FFF6F4"

                                        with column:
                                            st.markdown(
                                                f"<div style='background: {card_bg}; border: 1px solid #E7D9CF; border-radius: 14px; padding: 14px 12px 12px 12px; min-height: 122px;'>"
                                                f"<div style='display: flex; justify-content: space-between; align-items: center; gap: 8px;'>"
                                                f"<div style='font-size: 15px; font-weight: 800; color: #1F1F1F; white-space: nowrap;'>{store}</div>"
                                                f"<span style='display: inline-block; background: {badge_bg}; color: {badge_color}; font-size: 10px; font-weight: 800; letter-spacing: 0.04em; padding: 4px 8px; border-radius: 999px; text-transform: uppercase; white-space: nowrap;'>{badge_text}</span>"
                                                f"</div>"
                                                f"<div style='margin-top: 12px; color: #444; font-size: 12px; line-height: 1.5;'>"
                                                f"<div>{failed_total}/{stats['total']} failed</div>"
                                                f"<div style='margin-top: 6px;'>timeouts: {stats['timeout']}</div>"
                                                f"<div>no match: {stats['not_found']}</div>"
                                                f"</div>"
                                                f"</div>",
                                                unsafe_allow_html=True,
                                            )

                            with st.expander("Store-by-store diagnostics"):
                                for detail in diagnostics:
                                    item_name = detail.get("item", "Unknown item")
                                    store = detail.get("store", "Unknown store")
                                    status = detail.get("status", "unavailable")
                                    message = detail.get("message", "Price unavailable")
                                    st.markdown(f"- **{store}** — **{item_name}** — {status}: {message}")
                        else:
                            st.error("No valid shopping-list items were found to compare.")
                        
            # --- RESULTS SCREEN ---
            if "report" in st.session_state and st.session_state.get("shopping_active", False):
                report = st.session_state["report"]
                
                st.markdown(f"""
                <div style="background-color: #005A36; color: white; padding: 30px 20px 20px 20px; margin: -60px -20px 20px -20px; border-radius: 0 0 20px 20px; display: flex; align-items: center; gap: 15px;">
                    <div style="font-size: 20px;">←</div>
                    <div>
                        <h1 style="margin: 0; color: white; font-size: 22px; font-weight: 800;">Price Comparison</h1>
                        <p style="margin: 0; font-size: 13px; opacity: 0.9;">{report['total_items']} items across {len(active_names)} stores</p>
                    </div>
                </div>
                """, unsafe_allow_html=True)
                
                tab_choice = "Overview"
                split_available = split_shopping_available(report)
                single_available = single_store_shopping_available(report)
                selected_mode = st.session_state.get("shop_mode")
                if (
                    (selected_mode == "split" and not split_available)
                    or (selected_mode == "single" and not single_available)
                ):
                    st.session_state["shop_mode"] = (
                        "single" if single_available else "overview"
                    )
                    st.rerun()

                unpriced_items = report.get("unpriced_items", [])
                if unpriced_items:
                    item_label = "item" if len(unpriced_items) == 1 else "items"
                    st.warning(
                        f"Could not find a reliable price for {len(unpriced_items)} {item_label}: "
                        + ", ".join(unpriced_items)
                    )

                if tab_choice == "Overview":
                    # -----------------------------------------------
                    # SUB-VIEW: SHOPPING SPLIT DETAILS / SINGLE DETAILS
                    # -----------------------------------------------
                    if st.session_state.get("shop_mode") in ["split", "single"]:

                        if st.button("← Back to Options", type="secondary", key="btn_back_options"):
                            st.session_state["shop_mode"] = "overview"
                            st.rerun()

                        if st.session_state["shop_mode"] == "split":
                            st.markdown("<p style='font-size: 13px; font-weight: 700; color: #666; margin-top: 15px; margin-bottom: 10px; text-transform: uppercase;'>YOUR SHOPPING SPLIT</p>", unsafe_allow_html=True)
                            active_cost = report["comparison_modes"]["split_store_optimal"]["total_cost"]
                        else:
                            st.markdown("<p style='font-size: 13px; font-weight: 700; color: #666; margin-top: 15px; margin-bottom: 10px; text-transform: uppercase;'>YOUR SINGLE STORE SHOP</p>", unsafe_allow_html=True)
                            active_cost = report["comparison_modes"]["single_store_best"]["total_cost"]

                        html_combined = (
                            f'<div style="background-color: #F6E7B9; border-radius: 12px; padding: 15px; display: flex; justify-content: space-between; align-items: center; margin-bottom: 25px;">'
                            f'<div style="font-weight: 800; color: #333; font-size: 16px;">Combined total</div>'
                            f'<div style="font-size: 20px; font-weight: 900; color: #005A36;">${active_cost:.2f}</div>'
                            f'</div>'
                        )
                        st.markdown(html_combined, unsafe_allow_html=True)

                        grouped_items = {}
                        if st.session_state["shop_mode"] == "split":
                            for item in report["item_breakdown"]:
                                store = item["cheapest_store"]
                                if store not in grouped_items:
                                    grouped_items[store] = []
                                grouped_items[store].append({
                                    "item_name": item["item_name"],
                                    "unit_price": item["unit_price"],
                                    "total_price": item["total_price"],
                                    "savings_vs_highest": item.get("savings_vs_highest", 0.0),
                                    "matched_name": next(
                                        (
                                            data.get("product_name")
                                            for store_name, data in item["all_stores"]
                                            if store_name == store
                                        ),
                                        "",
                                    ),
                                })
                        else:
                            best_store = report["comparison_modes"]["single_store_best"]["store_name"]
                            grouped_items[best_store] = []
                            for item in report["item_breakdown"]:
                                store_data = next((data for s_name, data in item["all_stores"] if s_name == best_store), None)
                                if store_data:
                                    grouped_items[best_store].append({
                                        "item_name": item["item_name"],
                                        "unit_price": store_data["unit_price"],
                                        "total_price": f"${store_data['total_price']:.2f}",
                                        "matched_name": store_data.get("product_name", ""),
                                    })

                        brand_colors = {
                            "Woolworths": "#005A36",
                            "Coles": "#E31837",
                            "Aldi": "#002D62",
                        }

                        for store_name, items in grouped_items.items():
                            b_color = brand_colors.get(store_name, "#555")
                            s_initial = store_name[0].upper()
                            store_total = sum(float(item['total_price'].replace('$', '')) for item in items)
                            store_selection_savings = round(
                                sum(item.get("savings_vs_highest", 0.0) for item in items),
                                2,
                            )

                            collected_count = 0
                            for idx, item in enumerate(items):
                                chk_key = f"chk_{st.session_state['shop_mode']}_{store_name}_{idx}"
                                if st.session_state.get(chk_key, False):
                                    collected_count += 1

                            with st.container(border=True):
                                st.markdown(f'''
                                <div style="background-color: {b_color}; color: white; padding: 15px; margin: -16px -16px 15px -16px; border-radius: 8px 8px 0 0; display: flex; justify-content: space-between; align-items: center;">
                                    <div style="display: flex; gap: 10px; align-items: center;">
                                        <div style="background: rgba(255,255,255,0.2); width: 32px; height: 32px; display: flex; justify-content: center; align-items: center; border-radius: 6px; font-weight: 800; font-size: 16px;">{s_initial}</div>
                                        <div>
                                            <div style="font-weight: 800; font-size: 16px;">{store_name}</div>
                                            <div style="font-size: 12px; opacity: 0.9;">{collected_count}/{len(items)} items collected</div>
                                            {f'<div style="font-size: 12px; font-weight: 700; margin-top: 2px;">Save up to ${store_selection_savings:.2f} here</div>' if store_selection_savings > 0 else ''}
                                        </div>
                                    </div>
                                    <div style="font-weight: 800; font-size: 18px;">${store_total:.2f}</div>
                                </div>
                                ''', unsafe_allow_html=True)

                                for idx, item in enumerate(items):
                                    st.markdown(
                                        '<div class="shopping-detail-item-marker"></div>',
                                        unsafe_allow_html=True,
                                    )
                                    c_select, c_name, c_price = st.columns([0.3, 3, 0.8])
                                    chk_key = f"chk_{st.session_state['shop_mode']}_{store_name}_{idx}"
                                    with c_select:
                                        st.checkbox(
                                            f'Select {item["item_name"]}',
                                            key=chk_key,
                                            label_visibility="collapsed",
                                        )
                                    with c_name:
                                        st.markdown(item["item_name"])
                                        matched_name = item.get("matched_name")
                                        if matched_name and matched_name.strip().lower() != item["item_name"].strip().lower():
                                            st.caption(f"Matched: {matched_name}")
                                    with c_price:
                                        st.markdown(
                                            f'<div class="shopping-detail-item-price">{item["total_price"]}</div>',
                                            unsafe_allow_html=True,
                                        )

                                    if idx < len(items) - 1:
                                        st.markdown("<hr style='margin: 0px 0 10px 0; opacity: 0.1;'>", unsafe_allow_html=True)

                    # -----------------------------------------------
                    # SUB-VIEW: OVERVIEW (DEFAULT)
                    # -----------------------------------------------
                    else:
                        option_count = int(single_available) + int(split_available)
                        if option_count > 1:
                            st.markdown("#### HOW WOULD YOU LIKE TO SHOP?")
                        elif option_count == 1:
                            st.markdown("#### YOUR AVAILABLE SHOPPING PLAN")
                        else:
                            st.markdown("#### NO COMPLETE SHOPPING PLAN")

                        single_best = report["comparison_modes"]["single_store_best"]
                        split_opt = report["comparison_modes"]["split_store_optimal"]

                        single_is_recommended = (
                            single_available
                            and single_best["total_cost"] <= split_opt["total_cost"]
                        )
                        if single_available:
                            single_title = "Shop at one store"
                            single_subtitle = f"Best of your stores: {single_best['store_name']}"
                            c1_border = "#F5A623" if single_is_recommended else "#E0E0E0"
                            c1_border_width = "2px" if single_is_recommended else "1px"
                            c1_badge = '<div style="position: absolute; top: -2px; right: 15px; background-color: #F5A623; color: black; font-weight: 800; font-size: 11px; padding: 4px 10px; border-radius: 0 0 8px 8px;">RECOMMENDED</div>' if single_is_recommended else ''
                            html_single = (
                                f'<div class="shopping-mode-banner" style="border: {c1_border_width} solid {c1_border};">'
                                f'{c1_badge}'
                                f'<div style="display: flex; justify-content: space-between; align-items: center; width: 100%; pointer-events: none;">'
                                f'<div style="display: flex; align-items: center; gap: 15px;">'
                                f'<div style="font-size: 26px;">🏪</div>'
                                f'<div style="line-height: 1.3;">'
                                f'<div style="font-weight: 800; color: #111; font-size: 16px;">{single_title}</div>'
                                f'<div style="font-size: 13px; color: #666;">{single_subtitle}</div>'
                                f'</div></div>'
                                f'<div style="font-size: 20px; font-weight: 800; color: #005A36;">${single_best["total_cost"]:.2f}</div>'
                                f'</div></div>'
                            )

                            st.markdown(html_single, unsafe_allow_html=True)
                            if st.button(
                                f"Choose {single_title}: {single_subtitle}, ${single_best['total_cost']:.2f}",
                                use_container_width=True,
                                key="btn_single",
                            ):
                                sheets_manager.log_user_event(user_id, "shop_mode_selected", mode="single")
                                st.session_state["shop_mode"] = "single"
                                st.rerun()
                        else:
                            if split_available:
                                st.info(
                                    "No single preferred store covers your complete basket. "
                                    "The split-store plan below covers the available items."
                                )
                            else:
                                st.warning(
                                    "The current matches cannot produce a complete shopping plan. "
                                    "Review the missing items and store coverage below."
                                )

                        if split_available:
                            selection_savings = report.get("price_selection_savings", {})
                            savings_amount = selection_savings.get("amount", 0.0)
                            compared_items = selection_savings.get("compared_items", 0)
                            if savings_amount > 0 and compared_items > 0:
                                compared_label = "item" if compared_items == 1 else "items"
                                st.markdown(
                                    '<div class="price-selection-savings">'
                                    '<div>'
                                    '<div class="price-selection-savings-label">GROCERY GECKO SAVING</div>'
                                    f'<div class="price-selection-savings-copy">Lowest available prices across {compared_items} comparable {compared_label}</div>'
                                    '</div>'
                                    f'<div class="price-selection-savings-amount">Save up to ${savings_amount:.2f}</div>'
                                    '</div>'
                                    '<p class="price-selection-savings-note">Compared item by item with the highest available price for the same products at your selected stores.</p>',
                                    unsafe_allow_html=True,
                                )

                            c2_border = "#F5A623" if not single_is_recommended else "#E0E0E0"
                            c2_border_width = "2px" if not single_is_recommended else "1px"
                            c2_badge = '<div style="position: absolute; top: -2px; right: 15px; background-color: #F5A623; color: black; font-weight: 800; font-size: 11px; padding: 4px 10px; border-radius: 0 0 8px 8px;">RECOMMENDED</div>' if not single_is_recommended else ''
                            html_split = (
                                f'<div class="shopping-mode-banner" style="border: {c2_border_width} solid {c2_border};">'
                                f'{c2_badge}'
                                f'<div style="display: flex; justify-content: space-between; align-items: center; width: 100%; pointer-events: none;">'
                                f'<div style="display: flex; align-items: center; gap: 15px;">'
                                f'<div style="font-size: 26px;">🛍️</div>'
                                f'<div style="line-height: 1.3;">'
                                f'<div style="font-weight: 800; color: #111; font-size: 16px;">Split across preferred stores</div>'
                                f'<div style="font-size: 13px; color: #666;">Buy each item where it\'s cheapest</div>'
                                f'</div></div>'
                                f'<div style="font-size: 20px; font-weight: 800; color: #005A36;">${split_opt["total_cost"]:.2f}</div>'
                                f'</div></div>'
                            )

                            st.markdown(html_split, unsafe_allow_html=True)
                            if st.button(
                                f"Choose split shopping across preferred stores, ${split_opt['total_cost']:.2f}",
                                use_container_width=True,
                                key="btn_split",
                            ):
                                sheets_manager.log_user_event(user_id, "shop_mode_selected", mode="split")
                                st.session_state["shop_mode"] = "split"
                                st.rerun()

                        if single_available:
                            st.markdown(
                                '<p class="store-ranking-heading">STORE RANKING — PRICE COVERAGE</p>',
                                unsafe_allow_html=True,
                            )

                        brand_colors = {
                            "Woolworths": "#005A36",
                            "Coles": "#E31837",
                            "Aldi": "#002D62",
                        }

                        max_cost = report["store_rankings"][-1]["total_cost"] if report["store_rankings"] else 1

                        for store in report["store_rankings"] if single_available else []:
                            s_name = store['store']
                            s_cost = store['total_cost']
                            b_color = brand_colors.get(s_name, "#555")
                            s_initial = s_name[0].upper()
                            is_best = store["is_complete"] and s_name == single_best["store_name"]
                            border_color = "#005A36" if is_best else "#E0E0E0"
                            border_width = "2px" if is_best else "1px"

                            if is_best:
                                diff_html = "<div style='color: #666; font-size: 12px; margin-top: 2px;'>Best price ✓</div>"
                                trophy = "🏆 "
                            else:
                                diff_html = f"<div style='color: #666; font-size: 12px; margin-top: 2px;'>{store['difference_from_best']}</div>"
                                trophy = ""

                            bar_width = min(100, int((s_cost / max_cost) * 100)) if max_cost > 0 else 100
                            html_card = (
                                f'<div style="border: {border_width} solid {border_color}; border-radius: 12px; padding: 15px; margin-bottom: 12px; background-color: #FFF;">'
                                f'<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">'
                                f'<div style="display: flex; align-items: center; gap: 15px;">'
                                f'<div style="background-color: {b_color}; color: white; width: 40px; height: 40px; border-radius: 8px; display: flex; justify-content: center; align-items: center; font-weight: 900; font-size: 20px;">'
                                f'{s_initial}'
                                f'</div>'
                                f'<div>'
                                f'<div style="font-weight: 800; color: #111; font-size: 16px;">{trophy}{s_name}</div>'
                                f'<div style="font-size: 13px; color: #888;">{store["coverage_count"]}/{store["coverage_total"]} items priced</div>'
                                f'</div></div>'
                                f'<div style="text-align: right;">'
                                f'<div style="font-size: 18px; font-weight: 800; color: #111;">${s_cost:.2f}</div>'
                                f'{diff_html}'
                                f'</div></div>'
                                f'<div style="width: 100%; background-color: #F0F0F0; height: 4px; border-radius: 2px;">'
                                f'<div style="width: {bar_width}%; background-color: {b_color}; height: 4px; border-radius: 2px;"></div>'
                                f'</div></div>'
                            )
                            st.markdown(html_card, unsafe_allow_html=True)

                can_finish_shop = (
                    st.session_state.get("shop_mode") in {"single", "split"}
                )
                if can_finish_shop:
                    st.markdown("<hr style='margin: 30px 0 15px 0; opacity: 0.2;'>", unsafe_allow_html=True)
                    checkout_checkbox_keys = shopping_checkbox_keys(
                        grouped_items,
                        st.session_state["shop_mode"],
                    )
                    all_items_collected = bool(checkout_checkbox_keys) and all(
                        st.session_state.get(key, False)
                        for key in checkout_checkbox_keys
                    )
                    st.markdown(
                        '<div class="checkout-completion-copy">'
                        '<span>AT CHECKOUT</span>'
                        '<strong>Forgot to tick items as you shopped?</strong>'
                        '<p>Mark the whole trolley as collected, then finish your shop.</p>'
                        '</div>',
                        unsafe_allow_html=True,
                    )
                    st.markdown('<div class="checkout-mark-all-marker"></div>', unsafe_allow_html=True)
                    st.button(
                        "All items collected" if all_items_collected else "Mark all items collected",
                        key="checkout_mark_all",
                        use_container_width=True,
                        disabled=all_items_collected,
                        on_click=mark_all_items_collected,
                        args=(st.session_state, checkout_checkbox_keys),
                    )
                if can_finish_shop and st.button("✅ Finish Shop", type="primary", use_container_width=True):
                    selected_purchase_names = set()
                    grouped_item_names = {}
                    if st.session_state.get("shop_mode") == "split":
                        for item in report["item_breakdown"]:
                            grouped_item_names.setdefault(item["cheapest_store"], []).append(item["item_name"])
                    else:
                        best_store = report["comparison_modes"]["single_store_best"]["store_name"]
                        grouped_item_names[best_store] = [
                            item["item_name"] for item in report["item_breakdown"]
                        ]

                    for store_name, item_names in grouped_item_names.items():
                        for item_index, item_name in enumerate(item_names):
                            check_key = f"chk_{st.session_state['shop_mode']}_{store_name}_{item_index}"
                            if st.session_state.get(check_key, False):
                                selected_purchase_names.add(item_name.strip().lower())

                    if not selected_purchase_names:
                        st.warning(
                            "Select at least one item you purchased before finishing your shop."
                        )
                    else:
                        with st.spinner("Archiving items to your recent shops database..."):
                            fresh_items = sheets_manager.get_shopping_list(user_id)
                            purchased_items = [
                                row for row in fresh_items
                                if row and row[0].strip().lower() in selected_purchase_names
                            ]
                            sheets_manager.archive_shop_to_history(
                                purchased_items,
                                st.session_state["current_user"]["email"],
                            )
                        st.session_state["recent_shops_available"] = True

                        report_savings = 0.0
                        report_total_items = None
                        if "report" in st.session_state:
                            if st.session_state.get("shop_mode") == "split":
                                report_savings = st.session_state["report"].get(
                                    "price_selection_savings", {}
                                ).get("amount", 0.0)
                            else:
                                report_savings = st.session_state["report"].get("trip_savings", 0.0)
                            report_total_items = st.session_state["report"].get("total_items")
                            st.session_state["last_savings"] = report_savings
                            del st.session_state["report"]
                        sheets_manager.log_user_event(
                            st.session_state["current_user"]["email"],
                            "shop_completed",
                            mode=st.session_state.get("shop_mode", ""),
                            items_ticked=len(selected_purchase_names),
                            items_total=report_total_items,
                            savings=report_savings,
                        )

                        st.session_state["shopping_active"] = False
                        st.session_state["current_page"] = "celebration"
                        time.sleep(0.5)
                        st.rerun()
                    
        # --- MAIN APP GLOBAL FOOTER ---
        st.markdown("<hr style='margin: 30px 0 20px 0; opacity: 0.1;'>", unsafe_allow_html=True)
        
        st.markdown('<div class="footer-buttons-marker"></div>', unsafe_allow_html=True)
        fc1, fc2, fc3, fc4 = st.columns(4, gap="small")
        with fc1:
            if st.button("Profile", key="footer_profile"):
                st.session_state["current_page"] = "profile"
                st.rerun()
        with fc2:
            if st.button("Privacy", key="footer_privacy"):
                st.session_state["current_page"] = "privacy"
                st.rerun()
        with fc3:
            if st.button("Support", key="footer_contact"):
                sheets_manager.log_user_event(st.session_state["current_user"]["email"], "contact_click")
                st.session_state["current_page"] = "contact"
                st.rerun()
        with fc4:
            if st.button("Refer", key="footer_refer"):
                sheets_manager.log_user_event(st.session_state["current_user"]["email"], "refer_click")
                st.session_state["current_page"] = "refer"
                st.rerun()
        st.markdown(
            f"<p class='footer-tagline'>© 2026 {BRAND_NAME} · {BRAND_TAGLINE}</p>",
            unsafe_allow_html=True,
        )

