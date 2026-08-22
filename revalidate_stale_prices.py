"""Refresh a bounded daily batch of stale Standard Prices entries."""

import concurrent.futures
import json
import os
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

from config import (
    GOOGLE_SCOPES,
    SPREADSHEET_ID,
    STALE_REVALIDATION_BATCH_LIMITS,
    STANDARD_PRICE_MAX_AGE_DAYS,
    THREAD_POOL_MAX_WORKERS,
)
from modules.brands import merge_brand_metadata
from modules.pricing import PriceScraper
from modules.revalidation import select_stale_standard_prices
from modules.sheets import SheetsManager


def require_environment_secret(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def build_dependencies() -> tuple[SheetsManager, PriceScraper]:
    apify_token = require_environment_secret("APIFY_TOKEN")
    zenrows_key = require_environment_secret("ZENROWS_KEY")
    credentials_json = require_environment_secret("GCP_SERVICE_ACCOUNT")
    credentials = Credentials.from_service_account_info(json.loads(credentials_json), scopes=GOOGLE_SCOPES)
    spreadsheet = gspread.authorize(credentials).open_by_key(SPREADSHEET_ID)
    sheets_manager = SheetsManager(spreadsheet)
    scraper = PriceScraper(apify_token, zenrows_key)
    scraper.usage_logger = lambda **kwargs: sheets_manager.log_scrape_run(
        source="stale_revalidation", **kwargs
    )
    return sheets_manager, scraper


def revalidate_stale_prices() -> None:
    sheets_manager, scraper = build_dependencies()
    standard_prices = sheets_manager.load_standard_prices()
    targets = select_stale_standard_prices(
        standard_prices,
        STALE_REVALIDATION_BATCH_LIMITS,
        STANDARD_PRICE_MAX_AGE_DAYS,
    )

    if not targets:
        print("No stale standard prices are due for revalidation.")
        return

    print(f"Revalidating {len(targets)} stale prices: {dict(STALE_REVALIDATION_BATCH_LIMITS)}")
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=THREAD_POOL_MAX_WORKERS) as executor:
        future_to_target = {
            executor.submit(
                scraper.get_live_price_result,
                store,
                item,
                max_search_candidates=1,
            ): (store, item, entry)
            for store, item, entry in targets
        }
        for future in concurrent.futures.as_completed(future_to_target):
            store, item, entry = future_to_target[future]
            try:
                results[(store, item)] = (entry, future.result())
            except Exception as error:
                print(f"{store}: {item} failed with {error}")

    successful = 0
    for (store, item), (entry, result) in results.items():
        price = result.get("price") if isinstance(result, dict) else None
        if price is None:
            print(f"{store}: {item} unavailable ({result.get('status', 'unknown')})")
            continue

        standard_prices[(store, item)] = {
            **entry,
            "price": price,
            "product_name": result.get("product_name") or entry.get("product_name") or item,
            "last_verified": datetime.now(),
            **merge_brand_metadata(entry, result),
            "barcode": result.get("barcode") or entry.get("barcode", ""),
            "source_url": result.get("source_url") or entry.get("source_url", ""),
        }
        successful += 1

    if successful:
        sheets_manager.save_standard_prices(standard_prices)
    print(f"Completed {successful}/{len(targets)} stale-price revalidations.")


if __name__ == "__main__":
    revalidate_stale_prices()
