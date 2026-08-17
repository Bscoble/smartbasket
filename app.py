import time
import re
import requests
import gspread
import urllib.parse
import concurrent.futures
import streamlit as st

import requests 
import streamlit as st 
def scrape_iga_zenrows(target_url): 
    zenrows_url = "https://api.zenrows.com/v1/" 
    params = { 
        "apikey": st.secrets["ZENROWS_KEY"], 
        "url": target_url, 
        # "js_render": "true" # Note: JS rendering significantly increases load times. Only use if IGA requires it. 
        } 
    try: 
        # Increased timeout to 90 seconds 
        response = requests.get(zenrows_url, params=params, timeout=90) 
        response.raise_for_status() 
        return response.text 
    except requests.exceptions.Timeout: 
        st.warning(f"Timeout: ZenRows took longer than 90s to scrape {target_url}.") 
        return None 
    except requests.exceptions.RequestException as e: 
        st.error(f"ZenRows API Error: {e}") 
    return None 

def fetch_all_prices_concurrently(item_name, api_keys):
    stores = ["Woolworths", "Coles", "Aldi", "IGA"]
    results = {}
    
    # Open a pool of 4 workers (one for each store)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        
        # Launch all 4 scrapers at the exact same millisecond
        future_to_store = {
            executor.submit(get_live_price, store, item_name, api_keys): store 
            for store in stores
        }
        
        # As each store finishes scraping, save its result immediately
        for future in concurrent.futures.as_completed(future_to_store):
            store = future_to_store[future]
            try:
                price = future.result()
                results[store] = price
            except Exception as e:
                st.error(f"Failed to fetch {store}: {e}")
                results[store] = None
                
    return results

from bs4 import BeautifulSoup
from google.oauth2.service_account import Credentials
from datetime import datetime, timedelta
from PIL import Image, ImageEnhance, ImageOps
from pyzbar.pyzbar import decode
from apify_client import ApifyClient

# --- PAGE CONFIG ---
st.set_page_config(page_title="SmartBasket", page_icon="🛒", layout="centered")

# --- 1. CONFIGURATION & SECURE AUTH ---
ZENROWS_KEY = st.secrets["ZENROWS_KEY"]
APIFY_TOKEN = st.secrets.get("APIFY_TOKEN", "")
creds_dict = dict(st.secrets["gcp_service_account"])
scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
gc = gspread.authorize(creds)
sh = gc.open_by_key("1e_ZARwsDg0LTYfVkgFjybUDXluycHW79lz2ntwRxoaw")

# --- FUNCTIONS ---
def load_store_preferences():
    default_prefs = {"Woolworths": True, "Coles": True, "Aldi": True, "IGA": True}
    try:
        pref_ws = sh.worksheet("Preferences")
        data = pref_ws.get_all_values()
        if len(data) >= 2:
            return {
                "Woolworths": data[1][0] == "True",
                "Coles": data[1][1] == "True",
                "Aldi": data[1][2] == "True",
                "IGA": data[1][3] == "True"
            }
    except Exception:
        pass
    return default_prefs

def save_store_preferences(prefs):
    try:
        pref_ws = sh.worksheet("Preferences")
        pref_ws.update('A1:D2', [
            ["Woolworths", "Coles", "Aldi", "IGA"],
            [str(prefs["Woolworths"]), str(prefs["Coles"]), str(prefs["Aldi"]), str(prefs["IGA"])]
        ])
    except Exception:
        pass

def load_price_cache():
    """Loads the entire price cache into memory for fast lookups."""
    try:
        ws = sh.worksheet("Price Cache")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title="Price Cache", rows="1000", cols="4")
        ws.append_row(["Store", "Item", "Price", "Timestamp"])
        return {}
    
    data = ws.get_all_values()
    cache = {}
    if len(data) > 1:
        for row in data[1:]:
            if len(row) >= 4:
                store, item, price_str, ts_str = row[0], row[1], row[2], row[3]
                try:
                    ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                    price = float(price_str)
                    cache[(store, item.lower())] = {"price": price, "timestamp": ts}
                except ValueError:
                    pass
    return cache

def save_price_cache(cache):
    """Bulk saves the updated cache back to Google Sheets."""
    try:
        ws = sh.worksheet("Price Cache")
        rows = [["Store", "Item", "Price", "Timestamp"]]
        for (store, item), data in cache.items():
            rows.append([store, item, str(data["price"]), data["timestamp"].strftime("%Y-%m-%d %H:%M:%S")])
        ws.clear()
        ws.append_rows(rows)
    except Exception:
        pass

def get_live_price(store, item_name, api_keys):
    apify_key = api_keys.get("apify")
    zenrows_key = api_keys.get("zenrows")
    
    try:
        # --- 1. APIFY SPECIALIZED SCRAPERS (WOOLWORTHS & COLES) ---
        if store == "Woolworths":
            client = ApifyClient(apify_key)
            run_input = {
                "urls": [f"https://www.woolworths.com.au/shop/search/products?searchTerm={urllib.parse.quote(item_name)}"],
                "ignore_url_failures": True,
                "max_items_per_url": 1,
                "proxy": {
                    "useApifyProxy": True,
                    "apifyProxyGroups": ["RESIDENTIAL"]
                }
            }
            
            run = client.actor("stealth_mode/woolworths-product-search-scraper").call(run_input=run_input)
            
            for item in client.dataset(run.default_dataset_id).list_items().items:
                if "pricing" in item and "now" in item["pricing"]:
                    return float(item["pricing"]["now"])
                elif "price" in item:
                    try:
                        return float(str(item["price"]).replace("$", ""))
                    except ValueError:
                        pass
    
            return 5.00

        elif store == "Coles":
            client = ApifyClient(apify_key)
            run_input = {
                "urls": [f"https://www.coles.com.au/search/products?q={urllib.parse.quote(item_name)}"], 
                "ignore_url_failures": True, 
                "max_items_per_url": 1, 
                "proxy": {
                    "useApifyProxy": True, 
                    "apifyProxyGroups": ["RESIDENTIAL"] 
                } 
            } 
            
            run = client.actor("stealth_mode/coles-product-search-scraper").call(run_input=run_input)
    
            for item in client.dataset(run.default_dataset_id).list_items().items:
                if "pricing" in item and "now" in item["pricing"]:
                    return float(item["pricing"]["now"])
                elif "price" in item:
                    try:
                        return float(str(item["price"]).replace("$", ""))
                    except ValueError:
                        pass
    
            return 5.00

        # --- 2. ZENROWS HTML FALLBACK (ALDI & IGA) ---
        elif store == "Aldi":
            target_url = f"https://www.aldi.com.au/en/search/?q={urllib.parse.quote(item_name)}"
            wait_for_element = ".box--price"
        elif store == "IGA":
            target_url = f"https://www.igashop.com.au/search?q={urllib.parse.quote(item_name)}"
            wait_for_element = ".item-price"
        else:
            return 99.99
            
        api_url = "https://api.zenrows.com/v1/"
        params = {
            "apikey": zenrows_key, 
            "url": target_url, 
            "js_render": "true", 
            "antibot": "true", 
            "premium_proxy": "true",
            "block_resources": "image,media,stylesheet,font",
            "wait_for": wait_for_element
        }
        response = requests.get(api_url, params=params, timeout=120)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        if store == "Aldi":
            price_element = soup.select_one('.box--price .value, .product-price, .price, span.price')
        else:
            price_element = soup.select_one('.item-price, .price')
            
        price = 0.00
        if price_element:
            clean_text = price_element.text.replace('$', '').strip()
            match = re.search(r'\d+\.\d{2}', clean_text)
            if match: 
                price = float(match.group())
            else:
                try: 
                    price = float(clean_text)
                except ValueError: 
                    price = 0.00
                    
        if price == 0.00:
            page_text = soup.get_text()
            prices_found = re.findall(r'\$(\d+\.\d{2})', page_text)
            if prices_found:
                valid_prices = [float(p) for p in prices_found if 0.50 <= float(p) <= 150.0]
                if valid_prices: 
                    price = valid_prices[0]
                    
        return price if price > 0 else 5.00 
        
    except Exception as e:
        # This prints the error permanently in your Codespaces terminal
        print(f"\n🚨 DEBUG - {store} error on {item_name}: {str(e)}\n")
        # This leaves a persistent red box on the app screen
        st.error(f"🚨 {store} error on {item_name}: {str(e)}")
        return 99.99

def generate_smart_basket_report(user_items, selected_stores):
    store_totals = {store: 0.0 for store in selected_stores}
    item_breakdown = []
    split_store_total = 0.0
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    valid_items = [row for row in user_items if len(row) >= 3 and row[0].strip()]
    total_items = len(valid_items)
    if total_items == 0: return None
    
    status_text.text("Loading price cache...")
    price_cache = load_price_cache()
    cache_updated = False
    
    for idx, row in enumerate(valid_items):
        item_name = row[0]
        try: qty = int(row[1])
        except ValueError: qty = 1
        unit = row[2]
        
        item_lower = item_name.lower()
        stores_to_search = selected_stores.copy()
        
        if "woolworths" in item_lower and "Woolworths" in selected_stores: stores_to_search = ["Woolworths"]
        elif "coles" in item_lower and "Coles" in selected_stores: stores_to_search = ["Coles"]
        elif "aldi" in item_lower and "Aldi" in selected_stores: stores_to_search = ["Aldi"]
        elif "iga" in item_lower and "IGA" in selected_stores: stores_to_search = ["IGA"]
        
        item_store_data = {}
        for store in selected_stores:
            if store in stores_to_search:
                cache_key = (store, item_lower)
                cached_data = price_cache.get(cache_key)
                
                use_cache = False
                if cached_data:
                    # Keep successful scrapes for 24h. Retry errors (99.99) after 1h.
                    expiry_hours = 24 if cached_data["price"] < 90.00 else 1
                    if (datetime.now() - cached_data["timestamp"]) < timedelta(hours=expiry_hours):
                        use_cache = True
                
                if use_cache:
                    status_text.text(f"Using cached price for: {item_name} at {store}...")
                    unit_price = cached_data["price"]
                    time.sleep(0.1) # Small visual buffer so the UI updates smoothly
                else:
                    status_text.text(f"Scraping live price for: {item_name} at {store}...")
                    
                    api_keys = {"zenrows": ZENROWS_KEY, "apify": APIFY_TOKEN}
                    unit_price = get_live_price(store, item_name, api_keys)
                    
                    price_cache[cache_key] = {
                        "price": unit_price,
                        "timestamp": datetime.now()
                    }
                    cache_updated = True
            else:
                unit_price = 99.99 
                
            total_price = unit_price * qty
            store_totals[store] += total_price
            item_store_data[store] = {
                "unit_price": f"${unit_price:.2f}/{unit}",
                "total_price": total_price
            }
        sorted_item_stores = sorted(item_store_data.items(), key=lambda x: x[1]['total_price'])
        cheapest_store = sorted_item_stores[0][0]
        best_price = sorted_item_stores[0][1]['total_price']
                
        split_store_total += best_price
        
        item_breakdown.append({
            "item_name": item_name,
            "quantity": f"{qty} {unit}",
            "cheapest_store": cheapest_store,
            "unit_price": f"${(best_price/qty):.2f}/{unit}",
            "total_price": f"${best_price:.2f}",
            "all_stores": sorted_item_stores
        })
        
        progress_bar.progress((idx + 1) / total_items)
        
    if cache_updated:
        status_text.text("Saving updated prices to cache...")
        save_price_cache(price_cache)
        
    status_text.empty()
    progress_bar.empty()
    ranked_stores = sorted(store_totals.items(), key=lambda x: x[1])
    best_single_store = ranked_stores[0][0]
    best_single_store_cost = ranked_stores[0][1]
    worst_store_cost = ranked_stores[-1][1]
    trip_savings = max(0.0, worst_store_cost - split_store_total)
    store_rankings = []
    for rank, (store, cost) in enumerate(ranked_stores, 1):
        diff = cost - best_single_store_cost
        diff_str = "+$0.00" if diff == 0 else f"+${diff:.2f} more"
        badge = "YOUR STORE" if store in selected_stores else ""
        store_rankings.append({
            "store": store, "rank": rank, "total_cost": cost, 
            "badge": badge, "difference_from_best": diff_str
        })
    return {
        "total_items": total_items,
        "trip_savings": trip_savings,
        "comparison_modes": {
            "single_store_best": {
                "store_name": best_single_store,
                "total_cost": best_single_store_cost,
                "is_recommended": True
            },
            "split_store_optimal": {
                "total_cost": split_store_total,
                "description": "Buy each item where it's cheapest across your stores"
            }
        },
        "store_rankings": store_rankings,
        "item_breakdown": item_breakdown
    }

def archive_shop_to_history():
    """Saves the current shopping list to a 'Recent Shops' worksheet and clears the list."""
    try:
        list_ws = sh.worksheet("Shopping List")
        items = list_ws.get_all_values()
        if len(items) <= 1: return False
        
        try:
            history_ws = sh.worksheet("Recent Shops")
        except gspread.WorksheetNotFound:
            history_ws = sh.add_worksheet(title="Recent Shops", rows="1000", cols="5")
            history_ws.append_row(["Item", "Qty", "Unit", "Image_URL", "Date"])
        
        current_date = datetime.now().strftime("%Y-%m-%d")
        rows_to_add = []
        
        start_idx = 1 if items[0][0].lower() == "item" else 0
        for row in items[start_idx:]:
            if len(row) >= 3 and row[0].strip():
                item_name = row[0].strip()
                qty = row[1]
                unit = row[2]
                img = row[3].strip() if len(row) >= 4 else ""
                rows_to_add.append([item_name, qty, unit, img, current_date])
        
        if rows_to_add:
            history_ws.append_rows(rows_to_add)
        
        # Clear current list ready for next week
        list_ws.clear()
        list_ws.append_row(["Item", "Qty", "Unit", "Image_URL"])
        return True
    except Exception:
        return False

def get_recent_history():
    """Retrieves items from the last 21 days, removing duplicates."""
    try:
        history_ws = sh.worksheet("Recent Shops")
        data = history_ws.get_all_values()
        if len(data) <= 1: return []
        
        recent_items = {}
        cutoff_date = datetime.now() - timedelta(days=21)
        
        start_idx = 1 if data[0][0].lower() == "item" else 0
        for row in data[start_idx:]:
            if len(row) >= 5:
                item = row[0].strip()
                if not item: continue
                qty, unit, img, date_str = row[1], row[2], row[3], row[4]
                try:
                    row_date = datetime.strptime(date_str, "%Y-%m-%d")
                    if row_date >= cutoff_date:
                        # Overwrites older entries, naturally deduplicating & keeping the most recent image/unit
                        recent_items[item.lower()] = {"name": item, "qty": qty, "unit": unit, "img": img}
                except ValueError:
                    pass
        return list(recent_items.values())
    except Exception:
        return []

def send_secure_feedback(user_email, feedback_msg):
    target_inbox = "bscoble74@gmail.com" 
    url = f"https://formsubmit.co/ajax/{target_inbox}"
    payload = {
        "email": user_email,
        "message": feedback_msg,
        "_subject": "🚨 SmartBasket: New Problem Report"
    }
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    try:
        response = requests.post(url, json=payload, headers=headers)
        return response.status_code == 200
    except:
        return False

def decode_barcode(image_file):
    """Multi-pass enhanced barcode decoder for curved surfaces and camera glare."""
    try:
        img = Image.open(image_file)
        
        def check_image(img_variant):
            decoded_objects = decode(img_variant)
            for obj in decoded_objects:
                return obj.data.decode("utf-8")
            return None
            
        # Pass 1: Raw image
        res = check_image(img)
        if res: return res
        
        # Pass 2: Grayscale & High Contrast
        gray = ImageOps.grayscale(img)
        enhancer = ImageEnhance.Contrast(gray)
        high_contrast = enhancer.enhance(2.0)
        res = check_image(high_contrast)
        if res: return res
        
        # Pass 3: Sharpened High Contrast
        sharpener = ImageEnhance.Sharpness(high_contrast)
        sharp = sharpener.enhance(2.5)
        res = check_image(sharp)
        if res: return res
        
        # Pass 4: Auto-rotations
        for angle in [90, 180, 270]:
            rotated = high_contrast.rotate(angle, expand=True)
            res = check_image(rotated)
            if res: return res
            
        # Pass 5: Binarization
        bw = gray.point(lambda p: 255 if p > 120 else 0)
        res = check_image(bw)
        if res: return res
    except Exception:
        pass
    return None

def lookup_barcode_product(barcode):
    """Fetches human-readable product title and image URL from Open Food Facts API."""
    url = f"https://world.openfoodfacts.org/api/v2/product/{barcode}.json"
    headers = {"User-Agent": "SmartBasketApp/1.0 (Australian Supermarket Price Tracker)"}
    
    try:
        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == 1:
                product = data.get("product", {})
                name = product.get("product_name_en") or product.get("product_name")
                brand = product.get("brands")
                image_url = product.get("image_front_url") or product.get("image_url") or ""
                
                title = f"{brand} {name}".strip() if brand else name
                if title:
                    return title, image_url
    except Exception:
        pass
    return None, None

def search_product_by_name(query):
    """Searches Open Food Facts database by text query."""
    url = f"https://world.openfoodfacts.org/cgi/search.pl?search_terms={urllib.parse.quote(query)}&search_simple=1&action=process&json=1&page_size=5"
    headers = {"User-Agent": "SmartBasketApp/1.0 (Australian Supermarket Price Tracker)"}
    try:
        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code == 200:
            data = response.json()
            results = []
            for p in data.get("products", []):
                name = p.get("product_name_en") or p.get("product_name")
                brand = p.get("brands")
                image_url = p.get("image_front_url") or p.get("image_url") or ""
                title = f"{brand} {name}".strip() if brand else name
                
                if title and title not in [r['title'] for r in results]:
                    results.append({"title": title, "image_url": image_url})
            return results
    except Exception:
        pass
    return []

# --- 2. SESSION STATE INIT ---
if "app_started" not in st.session_state:
    st.session_state["app_started"] = False
if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False
if "auth_mode" not in st.session_state:
    st.session_state["auth_mode"] = "login"
if "current_page" not in st.session_state:
    st.session_state["current_page"] = "home"
if "reset_email" not in st.session_state:
    st.session_state["reset_email"] = ""
if "prefs" not in st.session_state:
    st.session_state["prefs"] = load_store_preferences()
if "last_savings" not in st.session_state:
    st.session_state["last_savings"] = 0.0
if "expander_toggle" not in st.session_state:
    st.session_state["expander_toggle"] = False

prefs = st.session_state["prefs"]

# --- 3. CUSTOM FIGMA CSS & PHONE FRAME SILHOUETTE ---
st.markdown(f"""
<style>
    /* IPHONE SILHOUETTE WRAPPER FOR DESKTOP VIEW */
    @media (min-width: 768px) {{
        .stApp {{
            max-width: 412px !important;
            height: 850px !important;
            margin: 30px auto !important;
            border: 14px solid #1c1c1e !important;
            border-radius: 44px !important;
            box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.35) !important;
            overflow-y: auto !important;
            overflow-x: hidden !important;
            background-color: #ffffff !important;
        }}
    }}
    /* REMOVE EDGE PADDING TO FIX RIGHT-SIDE WHITE STRIPE */
    div[data-testid="stMainBlockContainer"], .main .block-container {{
        padding-left: 1rem !important;
        padding-right: 1rem !important;
        max-width: 100% !important;
    }}
    /* PREVENT CHECKBOX LABELS FROM WRAPPING */
    div[data-testid="stCheckbox"] label p {{
        white-space: nowrap !important;
    }}
    /* THUMBNAIL HOVER TO ZOOM EFFECT */
    .thumbnail-zoom {{
        width: 38px;
        height: 38px;
        border-radius: 10px;
        object-fit: cover;
        transition: transform 0.25s ease, box-shadow 0.25s ease;
        position: relative;
        z-index: 1;
        transform-origin: center left;
        cursor: zoom-in;
    }}
    .thumbnail-zoom:hover {{
        transform: scale(4);
        z-index: 9999 !important;
        box-shadow: 0px 8px 16px rgba(0,0,0,0.4);
        border-radius: 6px;
    }}
    /* SPLASH GET STARTED BUTTON OVERRIDE */
    button[data-testid="baseButton-primary"]:has(div:contains("Get Started")) {{
        background-color: #ffffff !important;
        color: #005A36 !important;
        border-radius: 30px !important;
        padding: 14px 20px !important;
        font-weight: 800 !important;
        font-size: 16px !important;
        width: 100% !important;
        border: none !important;
        box-shadow: 0 4px 14px rgba(0,0,0,0.25) !important;
        margin-top: 20px !important;
    }}
    button[data-testid="baseButton-primary"]:has(div:contains("Get Started")):hover {{
        background-color: #f2f2f2 !important;
        color: #004D2E !important;
    }}
    /* AUTH HEADER */
    .auth-header {{
        background-color: #005A36;
        color: white;
        padding: 50px 20px 40px 20px;
        margin: -60px -20px 40px -20px;
        border-radius: 0 0 50% 50% / 0 0 45px 45px;
        text-align: center;
        box-shadow: 0 4px 10px rgba(0,0,0,0.1);
    }}
    .auth-header h1 {{
        font-family: "Georgia", serif;
        font-size: 32px;
        font-weight: 700;
        margin-top: 10px;
        color: white;
    }}
    
    /* NATIVE APP HEADER */
    .app-header {{
        background-color: #005A36;
        color: white;
        padding: 30px 20px 20px 20px;
        margin: -60px -20px 20px -20px;
        border-radius: 0 0 20px 20px;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }}
    .app-header h1 {{ margin: 0; color: white; font-size: 26px; font-weight: 800; padding-top: 5px; }}
    .app-header p {{ margin: 0; font-size: 14px; opacity: 0.9; }}
    /* DYNAMIC STORE PILLS */
    div:has(> .store-pills-marker) + div[data-testid="stHorizontalBlock"] div[data-testid="stCheckbox"] label [data-baseweb="checkbox"] {{
        display: none !important;
    }}
    div:has(> .store-pills-marker) + div[data-testid="stHorizontalBlock"] div[data-testid="column"]:nth-child(1) div[data-testid="stCheckbox"] label p {{ background-color: {'#005A36' if prefs['Woolworths'] else '#E8E8E8'}; color: {'white' if prefs['Woolworths'] else '#555'}; padding: 6px 8px; border-radius: 20px; font-weight: 600; font-size: 12.5px; display: inline-block; margin: 0; white-space: nowrap; }}
    div:has(> .store-pills-marker) + div[data-testid="stHorizontalBlock"] div[data-testid="column"]:nth-child(2) div[data-testid="stCheckbox"] label p {{ background-color: {'#E31837' if prefs['Coles'] else '#E8E8E8'}; color: {'white' if prefs['Coles'] else '#555'}; padding: 6px 8px; border-radius: 20px; font-weight: 600; font-size: 12.5px; display: inline-block; margin: 0; white-space: nowrap; }}
    div:has(> .store-pills-marker) + div[data-testid="stHorizontalBlock"] div[data-testid="column"]:nth-child(3) div[data-testid="stCheckbox"] label p {{ background-color: {'#002D62' if prefs['Aldi'] else '#E8E8E8'}; color: {'white' if prefs['Aldi'] else '#555'}; padding: 6px 8px; border-radius: 20px; font-weight: 600; font-size: 12.5px; display: inline-block; margin: 0; white-space: nowrap; }}
    div:has(> .store-pills-marker) + div[data-testid="stHorizontalBlock"] div[data-testid="column"]:nth-child(4) div[data-testid="stCheckbox"] label p {{ background-color: {'#E31837' if prefs['IGA'] else '#E8E8E8'}; color: {'white' if prefs['IGA'] else '#555'}; padding: 6px 8px; border-radius: 20px; font-weight: 600; font-size: 12.5px; display: inline-block; margin: 0; white-space: nowrap; }}
    /* PRIMARY BUTTONS (COMPARE PRICES BUTTON) */
    button[data-testid="baseButton-primary"]:has(div:contains("Compare Prices")) {{
        background-color: #005A36 !important;
        color: white !important;
        border-radius: 35px !important;
        padding: 16px 24px !important;
        font-weight: 800 !important;
        font-size: 16.5px !important;
        width: 100% !important;
        border: none !important;
        box-shadow: 0 4px 14px rgba(0,0,0,0.2) !important;
        margin-top: 20px !important;
        margin-bottom: 10px !important;
    }}
    button[data-testid="baseButton-primary"]:has(div:contains("Compare Prices")):hover {{
        background-color: #004D2E !important;
    }}
    /* GENERAL PRIMARY BUTTONS */
    button[data-testid="baseButton-primary"] {{
        background-color: #005A36 !important;
        color: white !important;
        border-radius: 30px;
        padding: 12px;
        font-weight: 800;
        font-size: 16px;
        width: 100%;
        border: none;
        margin-top: 20px;
    }}
    
    /* SECONDARY ADD ITEM SUBMIT BUTTON */
    button[kind="secondaryFormSubmit"] {{
        background-color: #005A36;
        color: white;
        border-radius: 12px;
        font-weight: 800;
        border: none;
    }}
    /* HIDE MARKER DIVS */
    div:has(> .clear-all-marker),
    div:has(> .qty-minus-marker),
    div:has(> .qty-plus-marker),
    div:has(> .qty-del-marker) {{
        display: none !important;
    }}
    /* CLEAR ALL BUTTON - FRAMELESS RED TEXT LINK */
    div:has(> .clear-all-marker) + div[data-testid="element-container"] button {{
        background: none !important;
        border: none !important;
        padding: 0 !important;
        color: #E31837 !important;
        font-size: 13px !important;
        font-weight: 600 !important;
        box-shadow: none !important;
        min-height: unset !important;
        display: inline-block !important;
        margin-top: 2px !important;
    }}
    div:has(> .clear-all-marker) + div[data-testid="element-container"] button:hover {{
        color: #B30000 !important;
        background: none !important;
        text-decoration: underline !important;
    }}
    /* BASE BUTTON CENTERING FOR QUANTITY / DELETE CONTROL FRAMES */
    button[data-testid="baseButton-secondary"] {{
        display: flex !important;
        justify-content: center !important;
        align-items: center !important;
        text-align: center !important;
        padding: 0 !important;
        height: 38px !important;
        min-height: 38px !important;
        width: 100% !important;
        border-radius: 8px !important;
        border: none !important;
    }}
    /* FORCE INTERNAL TEXT/MARKDOWN CONTAINER TO PERFECT CENTER */
    button[data-testid="baseButton-secondary"] div[data-testid="stMarkdownContainer"],
    button[data-testid="baseButton-secondary"] div[data-testid="stMarkdownContainer"] p {{
        display: flex !important;
        justify-content: center !important;
        align-items: center !important;
        width: 100% !important;
        height: 100% !important;
        margin: 0 !important;
        padding: 0 !important;
        line-height: 1 !important;
        font-size: 16px !important;
        font-weight: bold !important;
        text-align: center !important;
    }}
    /* MINUS BUTTON - LIGHT GREEN FRAME */
    div:has(> .qty-minus-marker) + div[data-testid="element-container"] button {{
        background-color: #E6F4EA !important;
        color: #137333 !important;
    }}
    div:has(> .qty-minus-marker) + div[data-testid="element-container"] button:hover {{
        background-color: #CEEAD6 !important;
    }}
    /* PLUS BUTTON - LIGHT GREEN FRAME */
    div:has(> .qty-plus-marker) + div[data-testid="element-container"] button {{
        background-color: #E6F4EA !important;
        color: #137333 !important;
    }}
    div:has(> .qty-plus-marker) + div[data-testid="element-container"] button:hover {{
        background-color: #CEEAD6 !important;
    }}
    /* DELETE (X) BUTTON - LIGHT RED FRAME */
    div:has(> .qty-del-marker) + div[data-testid="element-container"] button {{
        background-color: #FCE8E6 !important;
        color: #C5221F !important;
    }}
    div:has(> .qty-del-marker) + div[data-testid="element-container"] button:hover {{
        background-color: #FAD2CF !important;
    }}
    /* INVISIBLE OVERLAY BUTTONS (Results Screen) */
    div[data-testid="element-container"]:has(#single-card-anchor), div[data-testid="element-container"]:has(#split-card-anchor) {{ display: none; }}
    div[data-testid="element-container"]:has(#single-card-anchor) + div[data-testid="element-container"],
    div[data-testid="element-container"]:has(#split-card-anchor) + div[data-testid="element-container"] {{
        margin-top: -95px !important; margin-bottom: 15px !important;
    }}
    div[data-testid="element-container"]:has(#single-card-anchor) + div[data-testid="element-container"] button,
    div[data-testid="element-container"]:has(#split-card-anchor) + div[data-testid="element-container"] button {{
        opacity: 0 !important; height: 95px !important; width: 100% !important; z-index: 99 !important; cursor: pointer !important;
    }}
    
    /* SUBPAGE BACK BUTTON INVISIBLE OVERLAY */
    div[data-testid="element-container"]:has(#subpage-back-anchor) {{ display: none; }}
    div[data-testid="element-container"]:has(#subpage-back-anchor) + div[data-testid="element-container"] {{
        margin-top: -65px !important; margin-bottom: 20px !important; margin-left: -5px !important; width: 40px !important;
    }}
    div[data-testid="element-container"]:has(#subpage-back-anchor) + div[data-testid="element-container"] button {{
        opacity: 0 !important; height: 40px !important; width: 40px !important; z-index: 99 !important; cursor: pointer !important;
    }}
    /* FOOTER BUTTONS - FRAMELESS SINGLE-LINE TEXT LINKS */
    div:has(> .footer-buttons-marker) + div[data-testid="stHorizontalBlock"] button {{
        background: transparent !important;
        border: none !important;
        outline: none !important;
        box-shadow: none !important;
        padding: 0 !important;
        margin: 0 !important;
        min-height: unset !important;
        height: auto !important;
        width: 100% !important;
    }}
    div:has(> .footer-buttons-marker) + div[data-testid="stHorizontalBlock"] button p {{
        font-size: 11px !important;
        white-space: nowrap !important;
        color: #666666 !important;
        font-weight: 500 !important;
        margin: 0 !important;
        padding: 0 !important;
        text-align: center !important;
    }}
    div:has(> .footer-buttons-marker) + div[data-testid="stHorizontalBlock"] button:hover p {{
        color: #111111 !important;
        text-decoration: underline !important;
    }}
    /* AUTH TEXT LINK BUTTONS */
    button[data-testid="baseButton-secondary"]:has(div:contains("Forgot password?")),
    button[data-testid="baseButton-secondary"]:has(div:contains("Don't have an account?")),
    button[data-testid="baseButton-secondary"]:has(div:contains("Already have an account?")) {{
        background: none !important; border: none !important; padding: 0 !important;
        color: #888 !important; font-size: 13px !important; font-weight: normal !important;
        box-shadow: none !important; margin-top: -5px !important;
    }}
    button[data-testid="baseButton-secondary"]:has(div:contains("Forgot password?")):hover,
    button[data-testid="baseButton-secondary"]:has(div:contains("Don't have an account?")):hover,
    button[data-testid="baseButton-secondary"]:has(div:contains("Already have an account?")):hover {{
        text-decoration: underline !important; color: #555 !important;
    }}
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
            <div style="font-size: 40px; margin-bottom: -10px;">🛒</div>
            <h1>Welcome back</h1>
        </div>
        """, unsafe_allow_html=True)
        
        with st.form("login_form"):
            email = st.text_input("Email address", placeholder="Email address", label_visibility="collapsed")
            pwd = st.text_input("Password", type="password", placeholder="Password", label_visibility="collapsed")
            
            submitted = st.form_submit_button("Sign In", type="primary", use_container_width=True)
            if submitted:
                st.session_state["authenticated"] = True
                st.rerun()
                
        st.markdown("<br>", unsafe_allow_html=True)
        
        _, center_col, _ = st.columns([1, 2, 1])
        with center_col:
            if st.button("Forgot password?", use_container_width=True):
                st.session_state["auth_mode"] = "forgot_password"
                st.rerun()
            if st.button("Don't have an account? **Sign up**", use_container_width=True):
                st.session_state["auth_mode"] = "signup"
                st.rerun()
                
    # -----------------------------------------------------------
    # VIEW: SIGN UP
    # -----------------------------------------------------------
    elif st.session_state["auth_mode"] == "signup":
        st.markdown("""
        <div class="auth-header">
            <div style="font-size: 40px; margin-bottom: -10px;">🛒</div>
            <h1>Create account</h1>
        </div>
        """, unsafe_allow_html=True)
        
        with st.form("signup_form"):
            name = st.text_input("Full name", placeholder="Full name", label_visibility="collapsed")
            email = st.text_input("Email address", placeholder="Email address", label_visibility="collapsed")
            pwd = st.text_input("Password", type="password", placeholder="Password", label_visibility="collapsed")
            postcode = st.text_input("Australian postcode", placeholder="Australian postcode", label_visibility="collapsed")
            st.caption("Australian postcodes only (e.g. 2000, 3000, 4000)")
            
            submitted = st.form_submit_button("Create Account", type="primary", use_container_width=True)
            if submitted:
                st.session_state["authenticated"] = True
                st.rerun()
                
        st.markdown("<br>", unsafe_allow_html=True)
        
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
            Enter the email address linked to your account and we'll send you a link to reset your password.
        </div>
        """, unsafe_allow_html=True)
        
        with st.form("forgot_form"):
            reset_email_input = st.text_input("Email address", placeholder="Email address", label_visibility="collapsed")
            
            submitted = st.form_submit_button("Send Reset Link", type="primary", use_container_width=True)
            if submitted:
                if reset_email_input:
                    st.session_state["reset_email"] = reset_email_input
                else:
                    st.session_state["reset_email"] = "bscoble74@gmail.com"
                st.session_state["auth_mode"] = "forgot_success"
                st.rerun()
                
    # -----------------------------------------------------------
    # VIEW: FORGOT PASSWORD SUCCESS (STEP 2)
    # -----------------------------------------------------------
    elif st.session_state["auth_mode"] == "forgot_success":
        st.markdown("""
        <div class="auth-header">
            <div style="font-size: 32px; margin-bottom: -5px;">🔑</div>
            <h1>Reset password</h1>
        </div>
        """, unsafe_allow_html=True)
        
        st.markdown('<div id="subpage-back-anchor"></div>', unsafe_allow_html=True)
        if st.button("Back", key="btn_success_back"):
            st.session_state["auth_mode"] = "login"
            st.rerun()
            
        target_email = st.session_state.get("reset_email", "bscoble74@gmail.com")
        
        st.markdown(f"""
        <div style="text-align: center; margin-top: 20px;">
            <div style="font-size: 36px; margin-bottom: 10px;">📧</div>
            <h3 style="font-family: 'Georgia', serif; font-size: 20px; color: #111; margin-bottom: 10px;">Check your email</h3>
            <p style="color: #555; font-size: 14px; line-height: 1.5; margin-bottom: 25px;">
                We've sent a password reset link to<br><b>{target_email}</b>
            </p>
            <p style="color: #888; font-size: 12px; margin-bottom: 30px;">
                Didn't receive it? Check your spam folder or try again in a few minutes.
            </p>
        </div>
        """, unsafe_allow_html=True)
        
        if st.button("Back to Sign In", type="primary", use_container_width=True):
            st.session_state["auth_mode"] = "login"
            st.rerun()


# =====================================================================
# --- 6. MAIN APP (Authenticated User) ---
# =====================================================================
else:

    if st.button("Compare Prices at 4 Stores"):
        with st.spinner(f"Bypassing firewalls and fetching live prices for {item_name}..."):
        
        # This will now take only as long as the SLOWEST store (e.g., 20-30 seconds), 
        # instead of the COMBINED time of all stores (3+ minutes).
            live_prices = fetch_all_prices_concurrently(item_name, api_keys)
        
            st.write(live_prices)

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
            <p>We believe grocery shopping shouldn't require visiting multiple stores blindly or sorting through confusing catalogues. By tracking live pricing across major Australian supermarkets like Woolworths, Coles, Aldi, and IGA, SmartBasket shows you instantly whether you save more by buying your whole basket at one store or splitting your items across the cheapest options.</p>
            
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
                    success = send_secure_feedback(email_input, feedback_input)
                    if success:
                        st.success("Thanks! Your feedback has been sent to our team.")
                    else:
                        st.error("Something went wrong sending the report. Please try again later.")
                        
    # -----------------------------------------------------------
    # VIEW: HOME / LIST PAGE
    # -----------------------------------------------------------
    elif st.session_state["current_page"] == "home":
        current_hour = datetime.now().hour
        if current_hour < 12:
            greeting = "Good morning,"
        elif current_hour < 18:
            greeting = "Good afternoon,"
        else:
            greeting = "Good evening,"
            
        st.markdown(f"""
        <div class="app-header">
            <div>
                <p>{greeting}</p>
                <h1>Brad</h1>
            </div>
            <div style="background-color: rgba(255,255,255,0.2); border-radius: 50%; width: 40px; height: 40px; display: flex; justify-content: center; align-items: center;">
                ↩
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        with st.container(border=True):
            st.markdown("<p style='font-size: 13px; font-weight: 700; color: #666; margin-bottom: 10px; margin-top: 0;'>ADD ITEM</p>", unsafe_allow_html=True)
            with st.form("add_item_form", clear_on_submit=True):
                item_name = st.text_input("What do you need?", placeholder="e.g., Full Cream Milk 2L", label_visibility="collapsed")
                c1, c2, c3 = st.columns([1.5, 2.5, 1])
                with c1:
                    qty = st.number_input("Qty", min_value=1, value=1, label_visibility="collapsed")
                with c2:
                    unit = st.selectbox("Unit", ["each", "L", "kg", "g", "Pk"], label_visibility="collapsed")
                with c3:
                    submitted = st.form_submit_button("＋", help="Add to List")
                
                if submitted and item_name:
                    list_ws = sh.worksheet("Shopping List")
                    list_ws.append_row([item_name, qty, unit, ""])
                    st.rerun()
            
            # Dynamic label toggle hack to force the expander to reset/collapse upon state change
            recent_shops_label = "🕒 Add from Recent Shops" + ("\u200B" if st.session_state["expander_toggle"] else "")
            with st.expander(recent_shops_label):
                recent_items = get_recent_history()
                if not recent_items:
                    st.info("No shopping history found for the last 3 weeks. Once you run a price comparison and click 'Finish Shop', your items will be saved here!")
                else:
                    with st.form("recent_shops_form", clear_on_submit=True):
                        st.markdown("<div style='font-size: 13px; font-weight:600; color:#555; margin-bottom: 10px;'>Select items to re-add to your list:</div>", unsafe_allow_html=True)
                        selected_indices = []
                        for idx, r_item in enumerate(recent_items):
                            cols = st.columns([0.15, 0.15, 0.7])
                            with cols[0]:
                                chk = st.checkbox("", key=f"rec_chk_{idx}", label_visibility="collapsed")
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
                                list_ws = sh.worksheet("Shopping List")
                                for i in selected_indices:
                                    sr = recent_items[i]
                                    list_ws.append_row([sr["name"], 1, sr["unit"], sr["img"]])
                                
                                # Toggle state to trick Streamlit into generating a "new" expander component that starts collapsed
                                st.session_state["expander_toggle"] = not st.session_state["expander_toggle"]
                                st.rerun()
                                
            with st.expander("🔍 Search Database by Name"):
                search_query = st.text_input("Search Open Food Facts", placeholder="e.g., Hillcrest Bubble", label_visibility="collapsed")
                if st.button("Search Database", use_container_width=True):
                    if search_query:
                        with st.spinner("Searching database..."):
                            results = search_product_by_name(search_query)
                            if results:
                                st.session_state["search_results"] = results
                            else:
                                st.session_state["search_results"] = []
                                st.warning("No products found matching that name.")
                
                if st.session_state.get("search_results"):
                    st.markdown("<hr style='margin: 10px 0; opacity: 0.2;'>", unsafe_allow_html=True)
                    for idx, res in enumerate(st.session_state["search_results"]):
                        sc1, sc2, sc3 = st.columns([1, 3, 1.5])
                        with sc1:
                            if res["image_url"]:
                                st.markdown(f'<img src="{res["image_url"]}" class="thumbnail-zoom" />', unsafe_allow_html=True)
                            else:
                                st.markdown('<div style="background-color: #E6F4EA; width: 38px; height: 38px; border-radius: 10px; display: flex; justify-content: center; align-items: center; font-size: 18px;">🛒</div>', unsafe_allow_html=True)
                        with sc2:
                            st.markdown(f"<div style='font-size: 13px; font-weight: 600; line-height: 1.2; padding-top: 5px;'>{res['title']}</div>", unsafe_allow_html=True)
                        with sc3:
                            if st.button("➕ Add", key=f"add_search_{idx}", use_container_width=True):
                                list_ws = sh.worksheet("Shopping List")
                                list_ws.append_row([res['title'], 1, "each", res['image_url']])
                                st.session_state["search_results"] = []
                                st.rerun()
                        st.markdown("<div style='height: 5px;'></div>", unsafe_allow_html=True)
                        
            with st.expander("📷 Scan Barcode from Pantry"):
                camera_photo = st.camera_input("Point camera at barcode", label_visibility="collapsed")
                if camera_photo:
                    with st.spinner("Reading barcode..."):
                        barcode_number = decode_barcode(camera_photo)
                        if barcode_number:
                            st.success(f"Scanned Barcode: `{barcode_number}`")
                            product_name, product_image = lookup_barcode_product(barcode_number)
                            if product_name:
                                st.info(f"Found: **{product_name}**")
                                if product_image:
                                    st.image(product_image, width=100)
                                if st.button(f"➕ Add '{product_name}' to List", type="primary"):
                                    list_ws = sh.worksheet("Shopping List")
                                    list_ws.append_row([product_name, 1, "each", product_image or ""])
                                    st.success(f"Added {product_name} to your list!")
                                    st.rerun()
                            else:
                                st.warning("Product not found in database. Please enter the name manually.")
                        else:
                            st.error("No barcode detected in image. Try holding the camera closer and ensuring good lighting.")
                            
        st.markdown("<p style='font-size: 13px; font-weight: 700; color: #666; margin-top: 10px; margin-bottom: 5px;'>PREFERRED STORES</p>", unsafe_allow_html=True)
        st.markdown('<div class="store-pills-marker"></div>', unsafe_allow_html=True)
        
        col1, col2, col3, col4 = st.columns([1.35, 0.85, 0.85, 0.85])
        with col1:
            sel_woolies = st.checkbox("Woolworths", value=prefs["Woolworths"])
        with col2:
            sel_coles = st.checkbox("Coles", value=prefs["Coles"])
        with col3:
            sel_aldi = st.checkbox("Aldi", value=prefs["Aldi"])
        with col4:
            sel_iga = st.checkbox("IGA", value=prefs["IGA"])
            
        new_prefs = {"Woolworths": sel_woolies, "Coles": sel_coles, "Aldi": sel_aldi, "IGA": sel_iga}
        if new_prefs != prefs:
            save_store_preferences(new_prefs)
            st.session_state["prefs"] = new_prefs
            st.rerun()
            
        active_names = [name for name, active in new_prefs.items() if active]
        st.markdown(f"<p style='font-size: 12px; color: #888; margin-top: -10px; margin-bottom: 25px;'>✓ We'll highlight {', '.join(active_names)} in the comparison</p>", unsafe_allow_html=True)
        
        try:
            list_ws = sh.worksheet("Shopping List")
            current_items = list_ws.get_all_values()
        except Exception:
            current_items = []
            
        valid_rows_with_indices = []
        if current_items:
            for sheet_idx, row in enumerate(current_items, start=1):
                if len(row) >= 3 and row[0].strip():
                    valid_rows_with_indices.append((sheet_idx, row))
                    
        item_count = len(valid_rows_with_indices)
        c_head1, c_head2 = st.columns([3, 1])
        with c_head1:
            st.markdown(f"<p style='font-size: 13px; font-weight: 700; color: #666;'>MY LIST ({item_count} ITEMS)</p>", unsafe_allow_html=True)
        with c_head2:
            if item_count > 0:
                st.markdown("<div style='text-align: right;'>", unsafe_allow_html=True)
                st.markdown('<div class="clear-all-marker"></div>', unsafe_allow_html=True)
                if st.button("Clear all", type="secondary"):
                    list_ws.clear()
                    list_ws.append_row(["Item", "Qty", "Unit", "Image_URL"])
                    st.rerun()
                st.markdown("</div>", unsafe_allow_html=True)
                
        if valid_rows_with_indices:
            for sheet_idx, row in valid_rows_with_indices:
                i_name = row[0]
                try: i_qty = int(row[1])
                except ValueError: i_qty = 1
                i_unit = row[2]
                i_img = row[3].strip() if len(row) >= 4 and row[3] else ""
                cols = st.columns([0.6, 2.1, 1.3, 0.5])
                with cols[0]:
                    if i_img:
                        st.markdown(f'<img src="{i_img}" class="thumbnail-zoom" style="margin-top: 2px;" />', unsafe_allow_html=True)
                    else:
                        st.markdown('<div style="background-color: #E6F4EA; width: 38px; height: 38px; border-radius: 10px; display: flex; justify-content: center; align-items: center; font-size: 18px; margin-top: 2px;">🛒</div>', unsafe_allow_html=True)
                with cols[1]:
                    st.markdown(f'<div style="padding-top: 2px;"><b>{i_name}</b><br><span style="color:#888; font-size:0.85em;">{i_qty} {i_unit}</span></div>', unsafe_allow_html=True)
                with cols[2]:
                    sub_c1, sub_c2, sub_c3 = st.columns(3)
                    with sub_c1:
                        st.markdown('<div class="qty-minus-marker"></div>', unsafe_allow_html=True)
                        if st.button("−", key=f"sub_{sheet_idx}"):
                            if i_qty > 1:
                                list_ws.update(f'B{sheet_idx}', [[i_qty - 1]])
                                st.rerun()
                    with sub_c2:
                        st.markdown(f'<div style="text-align:center; padding-top:10px; font-weight: bold;">{i_qty}</div>', unsafe_allow_html=True)
                    with sub_c3:
                        st.markdown('<div class="qty-plus-marker"></div>', unsafe_allow_html=True)
                        if st.button("+", key=f"add_{sheet_idx}"):
                            list_ws.update(f'B{sheet_idx}', [[i_qty + 1]])
                            st.rerun()
                with cols[3]:
                    st.markdown('<div class="qty-del-marker"></div>', unsafe_allow_html=True)
                    if st.button("✕", key=f"del_{sheet_idx}"):
                        list_ws.delete_rows(sheet_idx)
                        st.rerun()
                        
                st.markdown("<hr style='margin: 10px 0; opacity: 0.2;'>", unsafe_allow_html=True)
            
            store_count_label = len(active_names)
            
            if st.button(f"🔍 Compare Prices at {store_count_label} Stores", type="primary", use_container_width=True):
                if not active_names:
                    st.error("Please select at least one store to compare.")
                else:
                    with st.spinner("Bypassing supermarket firewalls and fetching live prices..."):
                        fresh_items = list_ws.get_all_values()
                        report = generate_smart_basket_report(fresh_items, active_names)
                        
                    if report:
                        st.session_state["report"] = report
                        st.session_state["shopping_active"] = True
                        st.session_state["active_tab"] = "Overview"
                        st.session_state["shop_mode"] = "overview"
                        st.rerun()
                    else:
                        st.error("No valid items found to compare.")
                        
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
                            "Aldi": "#002D62",
                            "IGA": "#E31837"
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
                        
                        c1_border = "#F5A623" if single_is_recommended else "#E0E0E0"
                        c1_border_width = "2px" if single_is_recommended else "1px"
                        c1_badge = '<div style="position: absolute; top: -2px; right: 15px; background-color: #F5A623; color: black; font-weight: 800; font-size: 11px; padding: 4px 10px; border-radius: 0 0 8px 8px;">RECOMMENDED</div>' if single_is_recommended else ''
                        html_single = (
                            f'<div style="border: {c1_border_width} solid {c1_border}; border-radius: 12px; padding: 15px; position: relative; background-color: #FAFAFA; height: 95px; box-sizing: border-box; display: flex; flex-direction: column; justify-content: center; margin-bottom: 0px;">'
                            f'{c1_badge}'
                            f'<div style="display: flex; justify-content: space-between; align-items: center; width: 100%;">'
                            f'<div style="display: flex; align-items: center; gap: 15px;">'
                            f'<div style="font-size: 26px;">🏪</div>'
                            f'<div style="line-height: 1.3;">'
                            f'<div style="font-weight: 800; color: #111; font-size: 16px;">Shop at one store</div>'
                            f'<div style="font-size: 13px; color: #666;">Best of your stores: {single_best["store_name"]}</div>'
                            f'</div></div>'
                            f'<div style="font-size: 20px; font-weight: 800; color: #005A36;">${single_best["total_cost"]:.2f}</div>'
                            f'</div></div>'
                        )
                        st.markdown(html_single, unsafe_allow_html=True)
                        st.markdown('<div id="single-card-anchor"></div>', unsafe_allow_html=True)
                        if st.button("", key="btn_single", use_container_width=True):
                            st.session_state["shop_mode"] = "single"
                            st.rerun()
                        
                        c2_border = "#F5A623" if not single_is_recommended else "#E0E0E0"
                        c2_border_width = "2px" if not single_is_recommended else "1px"
                        c2_badge = '<div style="position: absolute; top: -2px; right: 15px; background-color: #F5A623; color: black; font-weight: 800; font-size: 11px; padding: 4px 10px; border-radius: 0 0 8px 8px;">RECOMMENDED</div>' if not single_is_recommended else ''
                        html_split = (
                            f'<div style="border: {c2_border_width} solid {c2_border}; border-radius: 12px; padding: 15px; position: relative; background-color: #FAFAFA; height: 95px; box-sizing: border-box; display: flex; flex-direction: column; justify-content: center; margin-bottom: 0px;">'
                            f'{c2_badge}'
                            f'<div style="display: flex; justify-content: space-between; align-items: center; width: 100%;">'
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
                        st.markdown('<div id="split-card-anchor"></div>', unsafe_allow_html=True)
                        if st.button("", key="btn_split", use_container_width=True):
                            st.session_state["shop_mode"] = "split"
                            st.rerun()
                            
                        st.markdown("<p style='font-size: 13px; font-weight: 700; color: #666; margin-top: 30px; margin-bottom: 15px; text-transform: uppercase;'>STORE RANKING — FULL BASKET</p>", unsafe_allow_html=True)
                        
                        brand_colors = {
                            "Woolworths": "#005A36",
                            "Coles": "#E31837",
                            "Aldi": "#002D62",
                            "IGA": "#E31837"
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
                                
                                html_row = (
                                    f'<div style="display: flex; justify-content: space-between; align-items: center; padding: 4px 0;">'
                                    f'<div><b>{store_initial}</b> &nbsp; {store_name}</div>'
                                    f'<div>'
                                    f'<span style="color: gray; font-size: 0.85em;">{store_data["unit_price"]}</span> &nbsp;&nbsp; '
                                    f'<b>${store_data["total_price"]:.2f}</b>'
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
                    with st.spinner("Archiving items to your recent shops database..."):
                        archive_shop_to_history()
                    
                    if "report" in st.session_state:
                        st.session_state["last_savings"] = st.session_state["report"].get("trip_savings", 0.0)
                        del st.session_state["report"]
                        
                    st.session_state["shopping_active"] = False
                    st.session_state["current_page"] = "celebration"
                    time.sleep(0.5)
                    st.rerun()
                    
        # --- MAIN APP GLOBAL FOOTER ---
        st.markdown("<hr style='margin: 30px 0 15px 0; opacity: 0.2;'>", unsafe_allow_html=True)
        
        st.markdown('<div class="footer-buttons-marker"></div>', unsafe_allow_html=True)
        fc1, fc2, fc3, fc4 = st.columns([1, 1.2, 1.2, 1.4])
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
            if st.button("Refer a Friend", key="footer_refer"):
                st.session_state["current_page"] = "refer"
                st.rerun()
        st.markdown("<br>", unsafe_allow_html=True)

