"""
One-time backfill script to populate the Standard Prices reference table.

Run this locally (not in CI) after adding ZENROWS_KEY, APIFY_TOKEN, and
gcp_service_account to .streamlit/secrets.toml:

    python backfill_standard_prices.py

It reuses the same PriceScraper/SheetsManager classes as the live app, so
results benefit from the existing relevance filtering and query retries
instead of duplicating scraping logic.
"""

import tomllib
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials

from config import SPREADSHEET_ID, GOOGLE_SCOPES
from modules.pricing import PriceScraper
from modules.sheets import SheetsManager

# Common items worth having a standard price for on day one.
STAPLES = [
    "Full Cream Milk 2L",
    "Bananas",
    "Chicken Tenders",
    "Freddo Frogs",
    "Free Range Eggs 12pk",
    "White Bread 700g",
    "Coca-Cola 2L",
    "Arnotts Tim Tam Original 200g",
    "Weet-Bix 750g",
    "Butter 500g",
]

STORES = ["Woolworths", "Coles", "Aldi"]


def load_secrets() -> dict:
    with open(".streamlit/secrets.toml", "rb") as f:
        return tomllib.load(f)


def build_sheets_manager(secrets: dict) -> SheetsManager:
    creds = Credentials.from_service_account_info(
        secrets["gcp_service_account"], scopes=GOOGLE_SCOPES
    )
    gc = gspread.authorize(creds)
    spreadsheet = gc.open_by_key(SPREADSHEET_ID)
    return SheetsManager(spreadsheet)


def backfill() -> None:
    secrets = load_secrets()
    scraper = PriceScraper(secrets.get("APIFY_TOKEN", ""), secrets.get("ZENROWS_KEY", ""))
    sheets_manager = build_sheets_manager(secrets)

    standard_prices = sheets_manager.load_standard_prices()
    succeeded = 0
    failed = 0

    for item_name in STAPLES:
        item_lower = item_name.lower()
        for store in STORES:
            result = scraper.get_live_price_result(store, item_name)
            price = result.get("price")
            if price is not None:
                standard_prices[(store, item_lower)] = {
                    "price": price,
                    "product_name": result.get("product_name") or item_name,
                    "last_verified": datetime.now(),
                }
                succeeded += 1
                print(f"OK    {store:<12} {item_name:<35} ${price:.2f}")
            else:
                failed += 1
                print(f"MISS  {store:<12} {item_name:<35} {result.get('message', 'unavailable')}")

    sheets_manager.save_standard_prices(standard_prices)
    print(f"\nDone. {succeeded} priced, {failed} missing. {len(standard_prices)} entries saved.")


if __name__ == "__main__":
    backfill()
