"""
SmartBasket - Australian Grocery Price Comparison Application
A Streamlit-based app that helps users compare prices across major supermarkets.
"""

import logging
import time
from datetime import datetime, timedelta
from urllib.parse import quote
import concurrent.futures

import streamlit as st
import gspread
from gspread.exceptions import APIError, SpreadsheetNotFound
from google.oauth2.service_account import Credentials

# Import configuration and modules
import config
from config import (
    APP_TITLE,
    APP_ICON,
    APP_LAYOUT,
    GOOGLE_SCOPES,
    SPREADSHEET_ID,
    THREAD_POOL_MAX_WORKERS,
    THREAD_POOL_TIMEOUT,
)
from helpers import (
    format_price,
    get_store_color,
    get_store_initial,
    get_greeting,
    validate_email,
    infer_unit,
)
from modules import SheetsManager, PriceScraper, BarcodeScanner, ProductLookup, FeedbackManager, AuthManager

# ============================================================================
# LOGGING SETUP
# ============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
logger.info("SmartBasket application starting")

# ============================================================================
# PAGE CONFIGURATION
# ============================================================================
st.set_page_config(page_title=APP_TITLE, page_icon=APP_ICON, layout=APP_LAYOUT)


def _build_sheets_connection_error(error: Exception) -> tuple[str, list[str]]:
    """Return a user-facing error summary and troubleshooting steps."""
    error_text = str(error).lower()

    if isinstance(error, KeyError) or "gcp_service_account" in error_text:
        return (
            "Database configuration is missing.",
            [
                "Add the 'gcp_service_account' secret to Streamlit secrets.",
                "If running locally, create .streamlit/secrets.toml with the service account fields.",
                "If running on Streamlit Cloud, open App Settings > Secrets and paste the same values.",
            ],
        )

    if isinstance(error, SpreadsheetNotFound):
        return (
            "Database spreadsheet could not be opened.",
            [
                "Confirm SPREADSHEET_ID is correct in config.py.",
                "Share the spreadsheet with the service-account client_email as Editor.",
                "Ensure the Users, Shopping List, and Price Cache sheets are accessible.",
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
try:
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = Credentials.from_service_account_info(creds_dict, scopes=GOOGLE_SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)
    sheets_manager = SheetsManager(sh)
    auth_manager = AuthManager(sh)
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
try:
    ZENROWS_KEY = st.secrets.get("ZENROWS_KEY", "")
    APIFY_TOKEN = st.secrets.get("APIFY_TOKEN", "")
    price_scraper = PriceScraper(APIFY_TOKEN, ZENROWS_KEY)
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
    restored_user = auth_manager.validate_session(auth_token) if auth_token else None
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
        st.session_state["prefs"] = sheets_manager.load_store_preferences()
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
    if logout_token:
        auth_manager.revoke_session(logout_token)
    st.query_params.pop("logout", None)
    st.query_params.pop("auth_token", None)
    st.session_state["authenticated"] = False
    st.session_state["current_user"] = None
    st.session_state["auth_mode"] = "login"
    st.rerun()

prefs = st.session_state["prefs"]

# ============================================================================
# HELPER FUNCTIONS FOR REPORT GENERATION
# ============================================================================


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
    store_has_complete_prices = {store: True for store in selected_stores}
    item_breakdown = []
    split_store_total = 0.0
    unavailable_reasons = []
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    # Filter valid items
    valid_items = [row for row in user_items if len(row) >= 3 and row[0].strip()]
    total_items = len(valid_items)
    if total_items == 0:
        return None
    
    # Load price cache
    status_text.text("Loading price cache...")
    price_cache = sheets_manager.load_price_cache()
    cache_updated = False
    
    for idx, row in enumerate(valid_items):
        item_name = row[0]
        try:
            qty = int(row[1])
        except ValueError:
            qty = 1
        unit = row[2]
        
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
        stores_needing_scrape = []
        
        # Check cache and determine which stores need fresh prices
        for store in selected_stores:
            if store in stores_to_search:
                cache_key = (store, item_lower)
                cached_data = price_cache.get(cache_key)
                
                if (
                    cached_data
                    and cached_data.get("price", config.DEFAULT_PRICE_FALLBACK) < config.PRICE_VALIDITY_THRESHOLD
                    and sheets_manager.is_cache_valid(cached_data)
                ):
                    item_store_prices[store] = cached_data["price"]
                    item_store_status[store] = {
                        "status": "cached",
                        "message": "Using a cached price",
                    }
                    logger.debug(f"Using cached price for {item_name} at {store}")
                else:
                    stores_needing_scrape.append(store)
            else:
                item_store_prices[store] = None
                item_store_status[store] = {
                    "status": "not_requested",
                    "message": "Not searched for this item",
                }
        
        # Fetch live prices for uncached items
        if stores_needing_scrape:
            status_text.text(f"Fetching live prices for: {item_name}...")
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=THREAD_POOL_MAX_WORKERS)
            future_to_store = {
                executor.submit(price_scraper.get_live_price_result, store, item_name): store
                for store in stores_needing_scrape
            }
            completed_stores = set()
            try:
                completed_futures = concurrent.futures.as_completed(
                    future_to_store,
                    timeout=THREAD_POOL_TIMEOUT,
                )
                for future in completed_futures:
                    store = future_to_store[future]
                    completed_stores.add(store)
                    try:
                        result = future.result()
                        if isinstance(result, dict):
                            price = result.get("price")
                            item_store_status[store] = {
                                "status": result.get("status", "unavailable"),
                                "message": result.get("message", "Price unavailable"),
                            }
                        else:
                            price = result
                            item_store_status[store] = {
                                "status": "ok" if price is not None else "unavailable",
                                "message": "Price found" if price is not None else "Price unavailable",
                            }
                    except Exception as e:
                        logger.error(f"Error getting price for {item_name} at {store}: {e}")
                        price = None
                        item_store_status[store] = {
                            "status": "scraper_error",
                            "message": "The supermarket scraper failed",
                        }

                    if price is not None and price >= config.PRICE_VALIDITY_THRESHOLD:
                        price = None

                    item_store_prices[store] = price
                    if price is not None:
                        price_cache[(store, item_lower)] = {
                            "price": price,
                            "timestamp": datetime.now(),
                        }
                        cache_updated = True
            except concurrent.futures.TimeoutError:
                pending_stores = set(stores_needing_scrape) - completed_stores
                logger.warning(
                    "Price lookup timed out for %s at %s; marking prices unavailable",
                    item_name,
                    ", ".join(sorted(pending_stores)),
                )
                for store in pending_stores:
                    item_store_prices[store] = None
                    item_store_status[store] = {
                        "status": "timeout",
                        "message": "The supermarket lookup timed out",
                    }
            finally:
                for future in future_to_store:
                    future.cancel()
                executor.shutdown(wait=False, cancel_futures=True)
        
        # Calculate store totals and item breakdown
        item_store_data = {}
        for store, unit_price in item_store_prices.items():
            if unit_price is None:
                store_has_complete_prices[store] = False
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
                }
                continue

            total_price = unit_price * qty
            store_totals[store] += total_price
            item_store_data[store] = {
                "unit_price": format_price(unit_price) + f"/{unit}",
                "total_price": total_price,
                "status": item_store_status.get(store, {}).get("status", "ok"),
                "message": item_store_status.get(store, {}).get("message", "Price found"),
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
            progress_bar.progress((idx + 1) / total_items)
            continue

        cheapest_store = available_item_stores[0][0]
        best_price = available_item_stores[0][1]["total_price"]
        
        split_store_total += best_price
        
        item_breakdown.append({
            "item_name": item_name,
            "quantity": f"{qty} {unit}",
            "cheapest_store": cheapest_store,
            "unit_price": f"{format_price(best_price/qty)}/{unit}",
            "total_price": format_price(best_price),
            "all_stores": sorted_item_stores,
        })
        
        progress_bar.progress((idx + 1) / total_items)
    
    # Save updated cache
    if cache_updated:
        status_text.text("Saving updated prices to cache...")
        sheets_manager.save_price_cache(price_cache)
    
    status_text.empty()
    progress_bar.empty()
    
    # Generate rankings
    ranked_stores = sorted(
        (
            (store, cost)
            for store, cost in store_totals.items()
            if store_has_complete_prices[store]
        ),
        key=lambda x: x[1],
    )
    if not ranked_stores:
        logger.warning("No stores returned complete prices for this comparison")
        st.session_state["comparison_diagnostics"] = unavailable_reasons
        return None
    best_single_store = ranked_stores[0][0]
    best_single_store_cost = ranked_stores[0][1]
    worst_store_cost = ranked_stores[-1][1]
    trip_savings = max(0.0, worst_store_cost - split_store_total)
    
    store_rankings = []
    for rank, (store, cost) in enumerate(ranked_stores, 1):
        diff = cost - best_single_store_cost
        diff_str = "+$0.00" if diff == 0 else f"+{format_price(diff)} more"
        store_rankings.append({
            "store": store,
            "rank": rank,
            "total_cost": cost,
            "difference_from_best": diff_str,
        })
    
    return {
        "total_items": total_items,
        "trip_savings": trip_savings,
        "comparison_modes": {
            "single_store_best": {
                "store_name": best_single_store,
                "total_cost": best_single_store_cost,
                "is_recommended": True,
            },
            "split_store_optimal": {
                "total_cost": split_store_total,
                "description": "Buy each item where it's cheapest across your stores",
            },
        },
        "store_rankings": store_rankings,
        "item_breakdown": item_breakdown,
    }


try:
    import pathlib
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
    st.markdown("""
    <style>
        .stApp {
            background-color: #005A36 !important;
        }
        div[data-testid="stMainBlockContainer"], .main .block-container {
            padding: 0 !important;
        }
        header[data-testid="stHeader"] {
            display: none !important;
        }
    </style>
    <div style="background-color: #005A36; padding: 180px 20px 30px 20px; text-align: center; color: white; box-sizing: border-box;">
        <div style="background: rgba(255, 255, 255, 0.15); width: 80px; height: 80px; border-radius: 20px; display: flex; align-items: center; justify-content: center; margin: 0 auto 25px auto; font-size: 38px; backdrop-filter: blur(5px);">
            🛒
        </div>
        <h1 style="font-family: 'Georgia', serif; font-size: 36px; font-weight: 700; margin: 0 0 10px 0; color: white;">SmartBasket</h1>
        <p style="font-size: 15px; opacity: 0.9; margin: 0 0 40px 0; font-weight: 400;">Shop smarter. Save every week.</p>
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
        st.markdown("""
        <div class="auth-header">
            <div class="auth-logo">🛒</div>
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
        st.markdown("""
        <div class="auth-header">
            <div class="auth-logo">🛒</div>
            <h1>Create account</h1>
            <p class="auth-subtitle">Save your list and shop smarter each week.</p>
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
    # VIEW: SHOP CELEBRATION (POST-FINISH)
    # -----------------------------------------------------------
    if st.session_state["current_page"] == "celebration":
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
            Know someone who could use a hand beating supermarket prices? Send them a quick invite to try SmartBasket.
        </div>
        """, unsafe_allow_html=True)
        
        savings = st.session_state.get("last_savings", 0.0)
        
        # Dynamically build the message body using the saved trip amount and dynamic signature 
        if savings > 0:
            default_msg = f"Hey you should give this a try. It saved me ${savings:.2f} on this weeks shop.\n\nCheers,\nBrad\n\nDownload SmartBasket:"
        else:
            default_msg = "Hey you should give this a try. It saves me money every week on my shop.\n\nCheers,\nBrad\n\nDownload SmartBasket:"
            
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
                <p style="margin: 0; font-size: 13px; opacity: 0.9;">SmartBasket Information</p>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        st.markdown('<div id="subpage-back-anchor"></div>', unsafe_allow_html=True)
        if st.button("Back", key="btn_about_back"):
            st.session_state["current_page"] = "home"
            st.rerun()
            
        st.markdown("""
        <div style="color: #444; font-size: 14px; line-height: 1.7; margin-top: 10px;">
            <h3 style="color: #005A36; font-size: 18px; margin-bottom: 10px;">Welcome to SmartBasket</h3>
            <p>SmartBasket is Australia's independent grocery price comparison companion, designed to help households cut through supermarket price hikes and make informed shopping choices.</p>
            
            <h4 style="color: #222; font-size: 15px; margin-top: 20px;">Our Mission</h4>
            <p>We believe grocery shopping shouldn't require visiting multiple stores blindly or sorting through confusing catalogues. By tracking live pricing across major Australian supermarkets like Woolworths, Coles, and Aldi, SmartBasket shows you instantly whether you save more by buying your whole basket at one store or splitting your items across the cheapest options.</p>
            
            <h4 style="color: #222; font-size: 15px; margin-top: 20px;">Built for Everyday Australians</h4>
            <p>Created to simplify weekly budgeting, SmartBasket puts full transparency back into your hands. No hidden fees, no corporate bias—just real-time data comparing your preferred local stores.</p>
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
            
            <p>SmartBasket respects your privacy and is committed to protecting any personal data you share with us. This policy outlines how your information is handled.</p>
            
            <h4 style="color: #222; font-size: 15px; margin-top: 20px;">1. Information We Collect</h4>
            <p>When you create an account or use SmartBasket, we collect your name, email address, postal location (postcode), store preferences, and custom shopping lists required to deliver accurate price comparisons.</p>
            
            <h4 style="color: #222; font-size: 15px; margin-top: 20px;">2. How We Use Your Data</h4>
            <p>Your data is used solely to provide and improve your app experience, such as saving your preferred shopping lists and configuring store comparisons. Feedback or problem reports submitted through the app are securely routed directly to our administrative team.</p>
            
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
            
            submitted = st.form_submit_button("Submit Feedback", type="primary", use_container_width=True)
            
            if submitted:
                if not email_input or not feedback_input:
                    st.error("Please fill in both fields so we can assist you properly.")
                else:
                    success = FeedbackManager.send_feedback(email_input, feedback_input)
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
            
        st.markdown(f"""
        <div class="app-header">
            <div>
                <p>{greeting}</p>
                <h1>{display_name}</h1>
            </div>
            <a class="header-logout-link" href="?logout=1&auth_token={st.query_params.get('auth_token', '')}" title="Log out" aria-label="Log out">⏻</a>
        </div>
        """, unsafe_allow_html=True)
        
        with st.container(border=True):
            st.markdown("<p style='font-size: 13px; font-weight: 700; color: #666; margin-bottom: 10px; margin-top: 0;'>ADD ITEM</p>", unsafe_allow_html=True)
            st.markdown('<div class="add-item-form-marker"></div>', unsafe_allow_html=True)
            with st.form("add_item_form", clear_on_submit=True):
                item_name = st.text_input(
                    "What do you need?",
                    placeholder="e.g., Helga's white bread 700g",
                    label_visibility="collapsed",
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
                    stored_unit = config.UNIT_VALUES[unit]
                    if stored_unit == "auto":
                        stored_unit = infer_unit(item_name)
                    sheets_manager.save_product(user_id, item_name)
                    if sheets_manager.add_item_to_list(item_name, int(qty), stored_unit, user_id=user_id):
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
                            local_results = sheets_manager.search_saved_products(user_id, item_name)
                            remote_results = (
                                ProductLookup.search_product_by_name(item_name)
                                if len(local_results) < 5
                                else []
                            )
                            local_titles = {result["title"].lower() for result in local_results}
                            st.session_state["search_results"] = (
                                local_results
                                + [result for result in remote_results if result["title"].lower() not in local_titles]
                            )[:5]
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
                                    st.markdown('<div style="background-color: #E6F4EA; width: 28px; height: 28px; border-radius: 6px; display: flex; justify-content: center; align-items: center; font-size: 14px; margin-top:-5px;">🛒</div>', unsafe_allow_html=True)
                            with cols[2]:
                                st.markdown(f'<div style="font-size: 14px; font-weight: 500; margin-top:-2px;">{r_item["name"]}</div>', unsafe_allow_html=True)
                            
                            st.markdown("<div style='height: 4px;'></div>", unsafe_allow_html=True)
                        
                        if st.form_submit_button("➕ Add Selected to List", use_container_width=True):
                            if selected_indices:
                                for i in selected_indices:
                                    sr = recent_items[i]
                                    sheets_manager.save_product(user_id, sr["name"], sr["img"])
                                    sheets_manager.add_item_to_list(
                                        sr["name"], int(sr["qty"]), sr["unit"], sr["img"], user_id
                                    )
                                
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
                                    '<div class="product-result-placeholder">🛒</div>',
                                    unsafe_allow_html=True,
                                )
                        with sc2:
                            st.markdown(f"<div style='font-size: 13px; font-weight: 600; line-height: 1.2; padding-top: 5px;'>{res['title']}</div>", unsafe_allow_html=True)
                        with sc3:
                            if st.button("➕ Add", key=f"add_search_{idx}", use_container_width=True):
                                sheets_manager.save_product(user_id, res["title"], res["image_url"])
                                if sheets_manager.add_item_to_list(
                                    res["title"], 1, "each", res["image_url"], user_id
                                ):
                                    st.session_state["search_performed"] = False
                                    st.session_state["search_results"] = []
                                    st.rerun()
                                else:
                                    st.error("Failed to add item. Please try again.")
                        st.markdown("<div style='height: 5px;'></div>", unsafe_allow_html=True)
                else:
                    st.info("No products found. Try adding a brand or pack size.")
                        
            with st.expander("📷 Scan Barcode from Pantry"):
                camera_photo = st.camera_input("Point camera at barcode", label_visibility="collapsed")
                if camera_photo:
                    with st.spinner("Reading barcode..."):
                        barcode_number = BarcodeScanner.decode_barcode(camera_photo)
                        if barcode_number:
                            st.success(f"Scanned Barcode: `{barcode_number}`")
                            product_name, product_image = ProductLookup.lookup_barcode_product(barcode_number)
                            if product_name:
                                st.info(f"Found: **{product_name}**")
                                if product_image:
                                    st.image(product_image, width=100)
                                if st.button(f"➕ Add '{product_name}' to List", type="primary"):
                                    sheets_manager.save_product(user_id, product_name, product_image or "")
                                    if sheets_manager.add_item_to_list(
                                        product_name, 1, "each", product_image or "", user_id
                                    ):
                                        st.success(f"Added {product_name} to your list!")
                                        st.session_state["search_performed"] = False
                                        st.session_state["search_results"] = []
                                        st.rerun()
                                    else:
                                        st.error("Failed to add item. Please try again.")
                            else:
                                st.warning("Product not found in database. Please enter the name manually.")
                        else:
                            st.error("No barcode detected in image. Try holding the camera closer and ensuring good lighting.")
                            
        st.markdown("<p style='font-size: 13px; font-weight: 700; color: #666; margin-top: 10px; margin-bottom: 5px;'>PREFERRED STORES</p>", unsafe_allow_html=True)
        st.markdown('<div class="store-pills-marker"></div>', unsafe_allow_html=True)
        
        col1, col2, col3 = st.columns([1.35, 0.85, 0.85])
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
                ):
                    updated_prefs = prefs.copy()
                    updated_prefs[store_name] = not prefs[store_name]
                    if sheets_manager.save_store_preferences(updated_prefs):
                        st.session_state["prefs"] = updated_prefs
                    st.rerun()

        new_prefs = st.session_state["prefs"]
            
        active_names = [name for name, active in new_prefs.items() if active]
        st.markdown(f"<p style='font-size: 12px; color: #888; margin-top: -10px; margin-bottom: 25px;'>✓ We'll highlight {', '.join(active_names)} in the comparison</p>", unsafe_allow_html=True)
        
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
            st.markdown(f"<p style='font-size: 13px; font-weight: 700; color: #666;'>MY LIST ({item_count} ITEMS)</p>", unsafe_allow_html=True)
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
                cols = st.columns([0.55, 1.9, 1.65, 0.55])
                with cols[0]:
                    if i_img:
                        st.markdown(f'<img src="{i_img}" class="thumbnail-zoom" style="margin-top: 2px;" />', unsafe_allow_html=True)
                    else:
                        st.markdown('<div style="background-color: #E6F4EA; width: 38px; height: 38px; border-radius: 10px; display: flex; justify-content: center; align-items: center; font-size: 18px; margin-top: 2px;">🛒</div>', unsafe_allow_html=True)
                with cols[1]:
                    st.markdown(f'<div style="padding-top: 2px;"><b>{i_name}</b><br><span style="color:#888; font-size:0.85em;">{i_qty} {i_unit}</span></div>', unsafe_allow_html=True)
                with cols[2]:
                    sub_c1, sub_c2, sub_c3 = st.columns([1, 1, 1], gap="small")
                    with sub_c1:
                        st.markdown('<div class="qty-minus-marker"></div>', unsafe_allow_html=True)
                        if st.button("−", key=f"sub_{sheet_idx}"):
                            if i_qty > 1:
                                sheets_manager.update_list_quantity(sheet_idx, i_qty - 1, user_id)
                                st.rerun()
                    with sub_c2:
                        st.markdown(f'<div class="qty-value">{i_qty}</div>', unsafe_allow_html=True)
                    with sub_c3:
                        st.markdown('<div class="qty-plus-marker"></div>', unsafe_allow_html=True)
                        if st.button("+", key=f"add_{sheet_idx}"):
                            sheets_manager.update_list_quantity(sheet_idx, i_qty + 1, user_id)
                            st.rerun()
                with cols[3]:
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
                    with st.spinner("Bypassing supermarket firewalls and fetching live prices..."):
                        report = generate_smart_basket_report(current_items, active_names)
                        
                    if report:
                        st.session_state["report"] = report
                        st.session_state["shopping_active"] = True
                        st.session_state["active_tab"] = "Overview"
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
                                summary_cols = st.columns(len(store_health))
                                for idx, (store, stats) in enumerate(sorted(store_health.items())):
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

                                    with summary_cols[idx]:
                                        st.markdown(
                                            f"<div style='background: {card_bg}; border: 1px solid #E7D9CF; border-radius: 14px; padding: 14px 12px 12px 12px; min-height: 122px;'>"
                                            f"<div style='display: flex; justify-content: space-between; align-items: center; gap: 8px;'>"
                                            f"<div style='font-size: 15px; font-weight: 800; color: #1F1F1F;'>{store}</div>"
                                            f"<span style='display: inline-block; background: {badge_bg}; color: {badge_color}; font-size: 10px; font-weight: 800; letter-spacing: 0.04em; padding: 4px 8px; border-radius: 999px; text-transform: uppercase;'>{badge_text}</span>"
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
                
                if "active_tab" not in st.session_state:
                    st.session_state["active_tab"] = "Overview"
                    
                tab_choice = st.radio("Navigation", ["Overview", "Breakdown", "Discount Cycle"], 
                                      index=["Overview", "Breakdown", "Discount Cycle"].index(st.session_state["active_tab"]),
                                      horizontal=True, label_visibility="collapsed", key="nav_radio")
                
                st.session_state["active_tab"] = tab_choice
                
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
                                if store not in grouped_items: grouped_items[store] = []
                                grouped_items[store].append({
                                    "item_name": item["item_name"],
                                    "unit_price": item["unit_price"],
                                    "total_price": item["total_price"]
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
                                        "total_price": f"${store_data['total_price']:.2f}"
                                    })
                                    
                        brand_colors = {
                            "Woolworths": "#005A36",
                            "Coles": "#E31837",
                            "Aldi": "#002D62"
                        }
                        
                        for store_name, items in grouped_items.items():
                            b_color = brand_colors.get(store_name, "#555")
                            s_initial = store_name[0].upper()
                            store_total = sum(float(item['total_price'].replace('$', '')) for item in items)
                            
                            # Dynamically calculate collected items count
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
                                        </div>
                                    </div>
                                    <div style="font-weight: 800; font-size: 18px;">${store_total:.2f}</div>
                                </div>
                                ''', unsafe_allow_html=True)
                                
                                for idx, item in enumerate(items):
                                    c1, c2 = st.columns([3, 1])
                                    chk_key = f"chk_{st.session_state['shop_mode']}_{store_name}_{idx}"
                                    with c1:
                                        st.checkbox(f"{item['item_name']} ({item['unit_price']})", key=chk_key)
                                    with c2:
                                        st.markdown(f"<div style='text-align: right; font-weight: 600; color: #333; margin-top: 5px;'>{item['total_price']}</div>", unsafe_allow_html=True)
                                    
                                    if idx < len(items) - 1:
                                        st.markdown("<hr style='margin: 0px 0 10px 0; opacity: 0.1;'>", unsafe_allow_html=True)
                    
                    # -----------------------------------------------
                    # SUB-VIEW: OVERVIEW (DEFAULT)
                    # -----------------------------------------------
                    else:
                        st.markdown("#### HOW WOULD YOU LIKE TO SHOP?")
                        
                        single_best = report["comparison_modes"]["single_store_best"]
                        split_opt = report["comparison_modes"]["split_store_optimal"]
                        
                        single_is_recommended = single_best["total_cost"] <= split_opt["total_cost"]
                        
                        # SINGLE STORE OPTION
                        c1_border = "#F5A623" if single_is_recommended else "#E0E0E0"
                        c1_border_width = "2px" if single_is_recommended else "1px"
                        c1_badge = '<div style="position: absolute; top: -2px; right: 15px; background-color: #F5A623; color: black; font-weight: 800; font-size: 11px; padding: 4px 10px; border-radius: 0 0 8px 8px;">RECOMMENDED</div>' if single_is_recommended else ''
                        html_single = (
                            f'<div style="border: {c1_border_width} solid {c1_border}; border-radius: 12px; padding: 15px; position: relative; background-color: #FAFAFA; height: 95px; box-sizing: border-box; display: flex; flex-direction: column; justify-content: center; cursor: pointer; transition: background-color 0.2s ease;" onmouseover="this.style.backgroundColor=\'#F5F5F5\';" onmouseout="this.style.backgroundColor=\'#FAFAFA\';">'
                            f'{c1_badge}'
                            f'<div style="display: flex; justify-content: space-between; align-items: center; width: 100%; pointer-events: none;">'
                            f'<div style="display: flex; align-items: center; gap: 15px;">'
                            f'<div style="font-size: 26px;">🏪</div>'
                            f'<div style="line-height: 1.3;">'
                            f'<div style="font-weight: 800; color: #111; font-size: 16px;">Shop at one store</div>'
                            f'<div style="font-size: 13px; color: #666;">Best of your stores: {single_best["store_name"]}</div>'
                            f'</div></div>'
                            f'<div style="font-size: 20px; font-weight: 800; color: #005A36;">${single_best["total_cost"]:.2f}</div>'
                            f'</div></div>'
                        )
                        
                        with st.form("single_store_form", clear_on_submit=False):
                            st.markdown(html_single, unsafe_allow_html=True)
                            submitted_single = st.form_submit_button("", use_container_width=True, label_visibility="collapsed", key="btn_single")
                            if submitted_single:
                                st.session_state["shop_mode"] = "single"
                                st.rerun()
                        
                        # SPLIT STORES OPTION
                        c2_border = "#F5A623" if not single_is_recommended else "#E0E0E0"
                        c2_border_width = "2px" if not single_is_recommended else "1px"
                        c2_badge = '<div style="position: absolute; top: -2px; right: 15px; background-color: #F5A623; color: black; font-weight: 800; font-size: 11px; padding: 4px 10px; border-radius: 0 0 8px 8px;">RECOMMENDED</div>' if not single_is_recommended else ''
                        html_split = (
                            f'<div style="border: {c2_border_width} solid {c2_border}; border-radius: 12px; padding: 15px; position: relative; background-color: #FAFAFA; height: 95px; box-sizing: border-box; display: flex; flex-direction: column; justify-content: center; cursor: pointer; transition: background-color 0.2s ease;" onmouseover="this.style.backgroundColor=\'#F5F5F5\';" onmouseout="this.style.backgroundColor=\'#FAFAFA\';">'
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
                        
                        with st.form("split_stores_form", clear_on_submit=False):
                            st.markdown(html_split, unsafe_allow_html=True)
                            submitted_split = st.form_submit_button("", use_container_width=True, label_visibility="collapsed", key="btn_split")
                            if submitted_split:
                                st.session_state["shop_mode"] = "split"
                                st.rerun()
                            
                        st.markdown("<p style='font-size: 13px; font-weight: 700; color: #666; margin-top: 30px; margin-bottom: 15px; text-transform: uppercase;'>STORE RANKING — FULL BASKET</p>", unsafe_allow_html=True)
                        
                        brand_colors = {
                            "Woolworths": "#005A36",
                            "Coles": "#E31837",
                            "Aldi": "#002D62"
                        }
                        
                        max_cost = report["store_rankings"][-1]["total_cost"] if report["store_rankings"] else 1
                        
                        for store in report["store_rankings"]:
                            s_name = store['store']
                            s_rank = store['rank']
                            s_cost = store['total_cost']
                            b_color = brand_colors.get(s_name, "#555")
                            s_initial = s_name[0].upper()
                            is_best = (s_rank == 1)
                            border_color = "#005A36" if is_best else "#E0E0E0"
                            border_width = "2px" if is_best else "1px"
                            
                            if is_best:
                                diff_html = "<div style='color: #666; font-size: 12px; margin-top: 2px;'>Best price ✓</div>"
                                trophy = "🏆 "
                            else:
                                diff_val = s_cost - report["comparison_modes"]["single_store_best"]["total_cost"]
                                diff_html = f"<div style='color: #E31837; font-size: 12px; font-weight: bold; margin-top: 2px;'>+${diff_val:.2f} more</div>"
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
                                f'<div style="font-size: 13px; color: #888;">#{s_rank} cheapest</div>'
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
                            
                elif tab_choice == "Breakdown":
                    st.markdown("#### ITEM-BY-ITEM BREAKDOWN")
                    
                    for item in report["item_breakdown"]:
                        with st.container(border=True):
                            st.markdown(f"**{item['item_name']}** &nbsp; <span style='color:gray; font-size:0.85em;'>× {item['quantity']}</span>", unsafe_allow_html=True)
                            
                            for store_idx, (store_name, store_data) in enumerate(item["all_stores"]):
                                store_initial = store_name[0].upper()
                                is_best = (store_idx == 0)
                                
                                best_badge = " &nbsp; <span style='background-color: #005A36; color: white; padding: 2px 8px; border-radius: 4px; font-size: 0.8em; font-weight: bold;'>BEST</span>" if is_best else ""
                                display_total = (
                                    f"${store_data['total_price']:.2f}"
                                    if store_data["total_price"] is not None
                                    else f"Unavailable: {store_data.get('message', 'Price unavailable')}"
                                )
                                
                                html_row = (
                                    f'<div style="display: flex; justify-content: space-between; align-items: center; padding: 4px 0;">'
                                    f'<div><b>{store_initial}</b> &nbsp; {store_name}</div>'
                                    f'<div>'
                                    f'<span style="color: gray; font-size: 0.85em;">{store_data["unit_price"]}</span> &nbsp;&nbsp; '
                                    f'<b>{display_total}</b>'
                                    f'{best_badge}'
                                    f'</div>'
                                    f'</div>'
                                )
                                st.markdown(html_row, unsafe_allow_html=True)
                                
                elif tab_choice == "Discount Cycle":
                    st.markdown("#### DISCOUNT CYCLE")
                    st.info("Discount cycle tracking is active and analyzing historical specials across your selected stores.")
                    
                # --- NEW FINISH SHOP BUTTON ---
                st.markdown("<hr style='margin: 30px 0 15px 0; opacity: 0.2;'>", unsafe_allow_html=True)
                if st.button("✅ Finish Shop & Save History", type="primary", use_container_width=True):
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

                        if "report" in st.session_state:
                            st.session_state["last_savings"] = st.session_state["report"].get("trip_savings", 0.0)
                            del st.session_state["report"]

                        st.session_state["shopping_active"] = False
                        st.session_state["current_page"] = "celebration"
                        time.sleep(0.5)
                        st.rerun()
                    
        # --- MAIN APP GLOBAL FOOTER ---
        st.markdown("<hr style='margin: 30px 0 20px 0; opacity: 0.1;'>", unsafe_allow_html=True)
        
        st.markdown('<div class="footer-buttons-marker"></div>', unsafe_allow_html=True)
        fc1, fc2, fc3, fc4 = st.columns(4, gap="small")
        with fc1:
            if st.button("About", key="footer_about"):
                st.session_state["current_page"] = "about"
                st.rerun()
        with fc2:
            if st.button("Privacy", key="footer_privacy"):
                st.session_state["current_page"] = "privacy"
                st.rerun()
        with fc3:
            if st.button("Support", key="footer_contact"):
                st.session_state["current_page"] = "contact"
                st.rerun()
        with fc4:
            if st.button("Refer", key="footer_refer"):
                st.session_state["current_page"] = "refer"
                st.rerun()
        st.markdown(
            "<p class='footer-tagline'>© 2026 SmartBasket · Shop Smarter, Save Every Week</p>",
            unsafe_allow_html=True,
        )

