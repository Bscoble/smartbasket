"""
Bulk backfill using broad category-style search keywords with resumable
pagination, instead of one Apify run per specific item name.

Validated approach (see investigation in chat history):
- Woolworths' "browse" category URLs do NOT paginate via ?pageNumber= (page 2
  silently returned the same 20 products as page 1, confirmed live).
- Woolworths' SEARCH endpoint DOES paginate correctly via &pageNumber=
  (confirmed live: page 2 of "searchTerm=biscuits" returned mostly new
  products, e.g. "Arnott's Nice Biscuits 250g").
- Coles direct category/listing URLs failed entirely (0 results, twice); its
  search endpoint is reliable and also supports broad keywords.
So both stores now use the same mechanism: broad keyword + search endpoint +
pageNumber pagination.

Each run advances a persistent "Crawl State" cursor per (store, keyword), so
coverage of a large category (e.g. 1,000+ "biscuits" results) builds up over
many scheduled runs instead of needing one huge run.

Run this locally after adding ZENROWS_KEY, APIFY_TOKEN, and gcp_service_account
to .streamlit/secrets.toml:

    python bulk_category_backfill.py
"""

import os
import json
import tomllib
from datetime import datetime
from urllib.parse import quote
import gspread
from google.oauth2.service_account import Credentials

from config import SPREADSHEET_ID, GOOGLE_SCOPES, STORES
from modules.pricing import PriceScraper
from modules.sheets import SheetsManager

MAX_ITEMS_PER_PAGE = 20
PAGES_PER_RUN = 3  # how many new pages to fetch per keyword, per store, per run

STORES_TO_CRAWL = ["Woolworths", "Coles"]

CATEGORY_KEYWORDS = [
    "dairy",
    "bakery",
    "meat",
    "fruit",
    "vegetables",
    "pantry",
    "drinks",
    "frozen",
    "household",
    "biscuits",
]


def load_secrets() -> dict:
    """Load secrets from environment variables (CI) or .streamlit/secrets.toml (local)."""
    apify_token = os.environ.get("APIFY_TOKEN", "").strip()
    zenrows_key = os.environ.get("ZENROWS_KEY", "").strip()
    gcp_json = os.environ.get("GCP_SERVICE_ACCOUNT", "").strip()

    if apify_token and gcp_json:
        return {
            "APIFY_TOKEN": apify_token,
            "ZENROWS_KEY": zenrows_key,
            "gcp_service_account": json.loads(gcp_json),
        }

    with open(".streamlit/secrets.toml", "rb") as f:
        return tomllib.load(f)


def build_sheets_manager(secrets: dict) -> SheetsManager:
    creds = Credentials.from_service_account_info(
        secrets["gcp_service_account"], scopes=GOOGLE_SCOPES
    )
    gc = gspread.authorize(creds)
    spreadsheet = gc.open_by_key(SPREADSHEET_ID)
    return SheetsManager(spreadsheet)


def build_page_urls(store: str, keyword: str, start_page: int, page_count: int) -> list:
    base = STORES[store]["search_url"].format(quote(keyword))
    if store == "Woolworths":
        # Confirmed live: &pageNumber= paginates correctly on the search endpoint.
        return [f"{base}&pageNumber={page}" for page in range(start_page, start_page + page_count)]
    # Coles: both &pageNumber= and &page= caused the scrape to fail (0 results,
    # confirmed live twice). Its actor has no documented pagination input, so
    # only the first page is fetched; broaden coverage via more keywords instead.
    return [base]


def crawl_keyword(
    scraper: PriceScraper,
    store: str,
    keyword: str,
    crawl_state: dict,
    standard_prices: dict,
    daily_specials: dict,
) -> int:
    state_key = (store, keyword)
    start_page = crawl_state.get(state_key, {}).get("last_page", 0) + 1 if store == "Woolworths" else 1
    urls = build_page_urls(store, keyword, start_page, PAGES_PER_RUN)
    pages_fetched = len(urls)

    print(f"[{store}] '{keyword}' pages {start_page}-{start_page + pages_fetched - 1}")
    products = scraper.get_bulk_products(store, urls, max_items=MAX_ITEMS_PER_PAGE)
    print(f"  -> {len(products)} products found")

    for product in products:
        key = (store, product["product_name"].strip().lower())
        standard_prices[key] = {
            "price": product["standard_price"],
            "product_name": product["product_name"],
            "last_verified": datetime.now(),
            "unit_price": product.get("unit_price"),
            "unit_label": product.get("unit_label", ""),
            "image_url": product.get("image_url", ""),
        }
        if product["is_special"] and product["price"] < product["standard_price"]:
            daily_specials[key] = {
                "price": product["price"],
                "product_name": product["product_name"],
            }

    crawl_state[state_key] = {
        "last_page": start_page + pages_fetched - 1 if store == "Woolworths" else 1,
        "last_run": datetime.now().isoformat(timespec="seconds"),
    }
    return len(products)


def backfill() -> None:
    secrets = load_secrets()
    scraper = PriceScraper(secrets.get("APIFY_TOKEN", ""), secrets.get("ZENROWS_KEY", ""))
    sheets_manager = build_sheets_manager(secrets)
    scraper.usage_logger = lambda **kw: sheets_manager.log_scrape_run(source="bulk_category_crawl", **kw)

    standard_prices = sheets_manager.load_standard_prices()
    daily_specials = sheets_manager.load_daily_specials()
    crawl_state = sheets_manager.load_crawl_state()

    total_added = 0
    for store in STORES_TO_CRAWL:
        for keyword in CATEGORY_KEYWORDS:
            total_added += crawl_keyword(
                scraper, store, keyword, crawl_state, standard_prices, daily_specials
            )

    sheets_manager.save_standard_prices(standard_prices)
    sheets_manager.save_daily_specials(daily_specials)
    sheets_manager.save_crawl_state(crawl_state)

    for store in STORES_TO_CRAWL:
        store_count = sum(1 for (s, _) in standard_prices if s == store)
        sheets_manager.log_catalog_size(store, store_count)

    print(
        f"\nDone. +{total_added} product scrapes this run. "
        f"{len(standard_prices)} standard entries, {len(daily_specials)} active specials, "
        f"{len(crawl_state)} crawl cursors saved."
    )


if __name__ == "__main__":
    backfill()
