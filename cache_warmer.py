import os
import re
import urllib.parse
import concurrent.futures
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import gspread
from google.oauth2.service_account import Credentials
from apify_client import ApifyClient
import json

# --- SECRETS SETUP (Pulls from Environment Variables in the cloud) ---
def require_environment_secret(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing required environment variable {name}. "
            "Add it under GitHub repository Settings > Secrets and variables > Actions."
        )
    return value


ZENROWS_KEY = require_environment_secret("ZENROWS_KEY")
APIFY_TOKEN = require_environment_secret("APIFY_TOKEN")
GCP_CREDS_JSON = require_environment_secret("GCP_SERVICE_ACCOUNT")

# Setup Google Sheets
try:
    creds_dict = json.loads(GCP_CREDS_JSON)
except json.JSONDecodeError as error:
    raise RuntimeError("GCP_SERVICE_ACCOUNT is not valid JSON.") from error
if not isinstance(creds_dict, dict) or not creds_dict.get("client_email") or not creds_dict.get("private_key"):
    raise RuntimeError("GCP_SERVICE_ACCOUNT is missing client_email or private_key.")
scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
gc = gspread.authorize(creds)
sh = gc.open_by_key("1e_ZARwsDg0LTYfVkgFjybUDXluycHW79lz2ntwRxoaw")
cache_ws = sh.worksheet("Price Cache")

# --- THE STAPLES LIST ---
COMMON_STAPLES = [
    "Full Cream Milk 2L",
    "Bananas",
    "Chicken Tenders",
    "Freddo Frogs",
    "Hillcrest Bubble",
    "Free Range Eggs 12pk"
]

STORES = ["Woolworths", "Coles", "Aldi"]
api_keys = {"zenrows": ZENROWS_KEY, "apify": APIFY_TOKEN}

def get_live_price(store, item_name, api_keys):
    apify_key = api_keys.get("apify")
    zenrows_key = api_keys.get("zenrows")
    
    try:
        # --- 1. APIFY SCRAPERS (WOOLWORTHS & COLES) ---
        if store in ["Woolworths", "Coles"]:
            client = ApifyClient(apify_key)
            actor = "stealth_mode/woolworths-product-search-scraper" if store == "Woolworths" else "stealth_mode/coles-product-search-scraper"
            search_url = f"https://www.woolworths.com.au/shop/search/products?searchTerm={urllib.parse.quote(item_name)}" if store == "Woolworths" else f"https://www.coles.com.au/search/products?q={urllib.parse.quote(item_name)}"
            
            run_input = {
                "urls": [search_url],
                "ignore_url_failures": True,
                "max_items_per_url": 1,
                "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]}
            }
            
            run = client.actor(actor).call(
                run_input=run_input,
                wait_duration=timedelta(seconds=75),
            )
            
            if run is None or run.status != "SUCCEEDED":
                return 99.99
            for item in client.dataset(run.default_dataset_id).list_items().items:
                products = item.get("products", [item]) if isinstance(item, dict) else []
                for product in products:
                    if "pricing" in product and "now" in product["pricing"]:
                        return float(product["pricing"]["now"])
                    if "price" in product:
                        try:
                            return float(str(product["price"]).replace("$", ""))
                        except ValueError:
                            pass
            return 5.00

        # --- 2. ZENROWS SCRAPER (ALDI) ---
        elif store == "Aldi":
            target_url = f"https://www.aldi.com.au/results?q={urllib.parse.quote(item_name)}"
            
            api_url = "https://api.zenrows.com/v1/"
            params = {
                "apikey": zenrows_key, 
                "url": target_url, 
                "js_render": "true", 
                "antibot": "true", 
                "premium_proxy": "true",
                "block_resources": "image,media,stylesheet,font",
                "wait": "5000",
            }
            
            response = requests.get(api_url, params=params, timeout=15)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            if store == "Aldi":
                # We skip printing the 800 characters here to keep the cloud logs clean!
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
            
        else:
            return 99.99
            
    except Exception as e:
        print(f"\n🚨 DEBUG - {store} error on {item_name}: {str(e)}\n")
        return 99.99

def warm_the_cache():
    print(f"Starting overnight cache warmup at {datetime.now()}...")
    
    # 1. Download current cache to memory
    data = cache_ws.get_all_values()
    cache = {}
    if len(data) > 1:
        for row in data[1:]:
            if len(row) >= 4:
                try:
                    ts = datetime.strptime(row[3], "%Y-%m-%d %H:%M:%S")
                    cache[(row[0], row[1].lower())] = {"price": float(row[2]), "timestamp": ts}
                except ValueError:
                    pass

    # 2. Scrape fresh prices concurrently
    for item in COMMON_STAPLES:
        print(f"Fetching fresh prices for: {item}")
        item_lower = item.lower()
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            future_to_store = {
                executor.submit(get_live_price, store, item, api_keys): store 
                for store in STORES
            }
            
            for future in concurrent.futures.as_completed(future_to_store):
                store = future_to_store[future]
                try:
                    price = future.result()
                    # Only update if the scrape was successful
                    if price < 90.00: 
                        cache[(store, item_lower)] = {
                            "price": price,
                            "timestamp": datetime.now()
                        }
                        print(f"✅ {store} updated {item}: ${price}")
                except Exception as e:
                    print(f"❌ Failed {store} for {item}: {e}")

    # 3. Bulk save the refreshed cache back to Google Sheets
    print("Saving updated prices to Google Sheets...")
    rows = [["Store", "Item", "Price", "Timestamp"]]
    for (store, item), item_data in cache.items():
        rows.append([store, item, str(item_data["price"]), item_data["timestamp"].strftime("%Y-%m-%d %H:%M:%S")])
    
    cache_ws.clear()
    cache_ws.append_rows(rows)
    print("Cache warmup complete!")

if __name__ == "__main__":
    warm_the_cache()

