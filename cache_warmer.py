"""
Overnight cache warmer for SmartBasket.

Reuses the shared PriceScraper/SheetsManager classes so this benefits from
the same relevance filtering, query retries, and product-name extraction as
the live app, instead of duplicating an older, naive scraping implementation.
"""

import os
import json
import concurrent.futures
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

from config import SPREADSHEET_ID, GOOGLE_SCOPES
from modules.brands import merge_brand_metadata
from modules.pricing import PriceScraper
from modules.sheets import SheetsManager


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

try:
    creds_dict = json.loads(GCP_CREDS_JSON)
except json.JSONDecodeError as error:
    raise RuntimeError("GCP_SERVICE_ACCOUNT is not valid JSON.") from error
if not isinstance(creds_dict, dict) or not creds_dict.get("client_email") or not creds_dict.get("private_key"):
    raise RuntimeError("GCP_SERVICE_ACCOUNT is missing client_email or private_key.")

creds = Credentials.from_service_account_info(creds_dict, scopes=GOOGLE_SCOPES)
gc = gspread.authorize(creds)
spreadsheet = gc.open_by_key(SPREADSHEET_ID)

sheets_manager = SheetsManager(spreadsheet)
price_scraper = PriceScraper(APIFY_TOKEN, ZENROWS_KEY)
price_scraper.usage_logger = lambda **kw: sheets_manager.log_scrape_run(source="cache_warmer", **kw)

STORES = ["Woolworths", "Coles", "Aldi"]
COMMON_STAPLES = [
    "Full Cream Milk 2L",
    "Bananas",
    "Chicken Tenders",
    "Freddo Frogs",
    "Hillcrest Bubble",
    "Free Range Eggs 12pk",
]


def warm_the_cache():
    print(f"Starting overnight cache warmup at {datetime.now()}...")

    cache = sheets_manager.load_price_cache()
    standard_prices = sheets_manager.load_standard_prices()

    for item in COMMON_STAPLES:
        print(f"Fetching fresh prices for: {item}")
        item_lower = item.lower()

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            future_to_store = {
                executor.submit(price_scraper.get_live_price_result, store, item): store
                for store in STORES
            }

            for future in concurrent.futures.as_completed(future_to_store):
                store = future_to_store[future]
                try:
                    result = future.result()
                    price = result.get("price")
                    if price is not None:
                        product_name = result.get("product_name") or item
                        now = datetime.now()
                        cache[(store, item_lower)] = {
                            "price": price,
                            "timestamp": now,
                            "product_name": product_name,
                        }
                        key = (store, item_lower)
                        existing = standard_prices.get(key, {})
                        standard_prices[key] = {
                            **existing,
                            "price": price,
                            "product_name": product_name,
                            "last_verified": now,
                            **merge_brand_metadata(existing, result),
                        }
                        print(f"✅ {store} updated {item}: ${price}")
                    else:
                        print(f"❌ {store} no price for {item}: {result.get('message', 'unavailable')}")
                except Exception as e:
                    print(f"❌ Failed {store} for {item}: {e}")

    print("Saving updated prices to Google Sheets...")
    sheets_manager.save_price_cache(cache)
    sheets_manager.save_standard_prices(standard_prices)
    print("Cache warmup complete!")


if __name__ == "__main__":
    warm_the_cache()

