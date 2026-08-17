import os
import re
import urllib.parse
import concurrent.futures
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
from apify_client import ApifyClient
import json

# --- SECRETS SETUP (Pulls from Environment Variables in the cloud) ---
ZENROWS_KEY = os.environ.get("ZENROWS_KEY")
APIFY_TOKEN = os.environ.get("APIFY_TOKEN")
GCP_CREDS_JSON = os.environ.get("GCP_SERVICE_ACCOUNT")

# Setup Google Sheets
creds_dict = json.loads(GCP_CREDS_JSON)
scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
gc = gspread.authorize(creds)
sh = gc.open_by_key("1e_ZARwsDg0LTYfVkgFjybUDXluycHW79lz2ntwRxoaw")
cache_ws = sh.worksheet("Price Cache")

# --- THE STAPLES LIST ---
# Add items here that you buy almost every week. 
# The script will ensure these always have fresh prices.
COMMON_STAPLES = [
    "Full Cream Milk 2L",
    "Bananas",
    "Chicken Tenders",
    "Freddo Frogs",
    "Hillcrest Bubble",
    "Free Range Eggs 12pk"
]

STORES = ["Woolworths", "Coles", "Aldi", "IGA"]
api_keys = {"zenrows": ZENROWS_KEY, "apify": APIFY_TOKEN}

# [PASTE YOUR EXACT get_live_price FUNCTION HERE]
# Copy the get_live_price function from your app.py 
# Just change any st.error() calls to print()

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

