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
import hashlib
import tomllib
from datetime import datetime, timedelta
from urllib.parse import quote
import gspread
from google.oauth2.service_account import Credentials

from config import SPREADSHEET_ID, GOOGLE_SCOPES, STORES
from build_dashboard import refresh_performance_dashboard
from modules.catalog_maintenance import merge_products
from modules.maintenance import setup_maintenance_scraper, BudgetExceeded, ProviderBlocked
from modules.pricing import PriceScraper
from modules.sheets import SheetsManager

MAX_ITEMS_PER_PAGE = 20
PAGES_PER_RUN = 3  # how many new pages to fetch per keyword, per store, per run

STORES_TO_CRAWL = ["Woolworths", "Coles", "Aldi"]

# Stores whose search endpoint has confirmed live pagination (i.e. requesting
# additional pages returns new, non-overlapping products rather than repeats).
PAGINATED_STORES = {"Woolworths", "Aldi"}

CATEGORY_TARGETS = [
    ("dairy", "Dairy, Eggs & Fridge"),
    ("bakery", "Bakery"),
    ("meat", "Meat & Seafood"),
    ("seafood", "Meat & Seafood"),
    ("fruit", "Fruit & Vegetables"),
    ("vegetables", "Fruit & Vegetables"),
    ("pantry", "Pantry"),
    ("drinks", "Drinks"),
    ("frozen", "Frozen"),
    ("household", "Cleaning & Household"),
    ("personal care", "Health & Beauty"),
    ("baby", "Baby"),
    ("pet food", "Pet Care"),
    ("deli", "Deli & Chilled Meats"),
    ("biscuits", "Snacks & Confectionery"),
]

# Coles' actor only returns the first search page reliably. Use specific, mapped
# queries instead of broad department terms so each successful search adds a
# distinct slice of the catalogue and retains a useful category when the actor
# does not return retailer category metadata.
COLES_CATALOG_TARGETS = [
    ("full cream milk", "Dairy"),
    ("lactose free milk", "Dairy"),
    ("cheese", "Dairy"),
    ("yogurt", "Dairy"),
    ("butter", "Dairy"),
    ("eggs", "Dairy"),
    ("bread", "Bakery"),
    ("wraps", "Bakery"),
    ("english muffins", "Bakery"),
    ("crumpets", "Bakery"),
    ("cakes", "Bakery"),
    ("bananas", "Fruit & Vegetables"),
    ("apples", "Fruit & Vegetables"),
    ("potatoes", "Fruit & Vegetables"),
    ("tomatoes", "Fruit & Vegetables"),
    ("salad", "Fruit & Vegetables"),
    ("chicken", "Meat & Seafood"),
    ("beef", "Meat & Seafood"),
    ("pork", "Meat & Seafood"),
    ("lamb", "Meat & Seafood"),
    ("sausages", "Meat & Seafood"),
    ("seafood", "Meat & Seafood"),
    ("rice", "Pantry"),
    ("pasta", "Pantry"),
    ("cereal", "Pantry"),
    ("coffee", "Pantry"),
    ("tea", "Pantry"),
    ("canned tomatoes", "Pantry"),
    ("cooking oil", "Pantry"),
    ("flour", "Pantry"),
    ("sugar", "Pantry"),
    ("biscuits", "Pantry"),
    ("chips", "Pantry"),
    ("soft drinks", "Drinks"),
    ("juice", "Drinks"),
    ("water", "Drinks"),
    ("sports drinks", "Drinks"),
    ("frozen pizza", "Frozen"),
    ("ice cream", "Frozen"),
    ("frozen meals", "Frozen"),
    ("frozen vegetables", "Frozen"),
    ("laundry detergent", "Household"),
    ("dishwashing", "Household"),
    ("toilet paper", "Household"),
    ("paper towels", "Household"),
    ("cleaning products", "Household"),
    ("bin bags", "Household"),
    ("personal care", "Health & Beauty"),
    ("baby products", "Baby"),
    ("pet food", "Pet Care"),
]
COLES_TARGETS_PER_RUN = 10
EXPANDED_COLES_TARGETS_PER_RUN = 25
EXPANDED_TARGETS_PER_RUN = 20
EXPANDED_CATEGORY_TARGETS = CATEGORY_TARGETS + COLES_CATALOG_TARGETS + [
    ("chocolate", "Snacks & Confectionery"),
    ("crackers", "Snacks & Confectionery"),
    ("baking mixes", "Pantry"),
    ("facial tissues", "Cleaning & Household"),
    ("shampoo", "Health & Beauty"),
    ("toothpaste", "Health & Beauty"),
    ("nappies", "Baby"),
    ("baby formula", "Baby"),
    ("dry dog food", "Pet Care"),
    ("wet cat food", "Pet Care"),
    ("Schweppes lemonade", "Drinks"),
    ("Arnott's Shapes", "Snacks & Confectionery"),
    ("McCain pizza", "Frozen"),
    ("Cadbury chocolate", "Snacks & Confectionery"),
    ("vanilla cupcake mix", "Pantry"),
    ("full cream milk 2L", "Dairy, Eggs & Fridge"),
    ("cherry tomatoes 250g", "Fruit & Vegetables"),
    ("pork sausages 550g", "Meat & Seafood"),
    ("apple juice 2L", "Drinks"),
    ("facial tissues 224 pack", "Cleaning & Household"),
    ("Cadbury Dairy Milk 315g", "Snacks & Confectionery"),
    ("McCain pepperoni pizza 490g", "Frozen"),
    ("Schweppes lemonade 30 pack", "Drinks"),
]
EXPANDED_CATEGORY_TARGETS = list(dict(EXPANDED_CATEGORY_TARGETS).items())
EXPANDED_COLES_TARGETS = list(dict(
    COLES_CATALOG_TARGETS + EXPANDED_CATEGORY_TARGETS
).items())


class CrawlPageError(RuntimeError):
    """A listing request failed without advancing its page cursor."""


def expansion_enabled() -> bool:
    return os.environ.get("EXPANDED_CATALOG_ENABLED", "").lower() == "true"


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
    if store == "Aldi":
        # Confirmed live: &page= paginates correctly on the results endpoint
        # (page 2 returned 30 entirely non-overlapping SKUs vs page 1).
        return [f"{base}&page={page}" for page in range(start_page, start_page + page_count)]
    # Coles: both &pageNumber= and &page= caused the scrape to fail (0 results,
    # confirmed live twice). Its actor has no documented pagination input, so
    # only the first page is fetched; broaden coverage via more keywords instead.
    return [base]


def get_coles_catalog_targets(crawl_state: dict) -> list:
    """Return the next bounded batch of mapped Coles queries, wrapping around."""
    cursor = crawl_state.get(("Coles", "__catalog_cursor__"), {}).get("last_page", 0)
    targets = EXPANDED_COLES_TARGETS if expansion_enabled() else COLES_CATALOG_TARGETS
    limit = EXPANDED_COLES_TARGETS_PER_RUN if expansion_enabled() else COLES_TARGETS_PER_RUN
    return [targets[(cursor + offset) % len(targets)] for offset in range(limit)]


def crawl_keyword(
    scraper: PriceScraper,
    store: str,
    keyword: str,
    crawl_state: dict,
    standard_prices: dict,
    daily_specials: dict,
    category_fallback: str = "",
    checkpoint=None,
    seen_products=None,
) -> int:
    state_key = (store, keyword)
    state = crawl_state.get(state_key, {})
    retry_after = state.get("retry_after")
    if retry_after and datetime.fromisoformat(retry_after) > datetime.now():
        return 0
    start_page = (
        state.get("last_page", 0) + 1
        if store in PAGINATED_STORES
        else 1
    )
    urls = build_page_urls(store, keyword, start_page, PAGES_PER_RUN)
    found = 0
    for offset, url in enumerate(urls):
        page = start_page + offset
        print(f"[{store}] '{keyword}' page {page}")
        result = scraper.get_bulk_products_result(store, url, max_items=MAX_ITEMS_PER_PAGE)
        state = crawl_state.get(state_key, {})
        if result["status"] == "error":
            raise CrawlPageError(
                f"{store}/{keyword} page {page} failed; cursor retained: {result['message']}"
            )
        if result["status"] in {"empty", "end"}:
            empty_runs = state.get("empty_runs", 0) + 1
            crawl_state[state_key] = {
                **state,
                "last_page": 0 if result["status"] == "end" or empty_runs >= 3 else state.get("last_page", 0),
                "page_signature": "" if result["status"] == "end" or empty_runs >= 3 else state.get("page_signature", ""),
                "last_run": datetime.now().isoformat(timespec="seconds"),
                "empty_runs": empty_runs,
                "retry_after": (datetime.now() + timedelta(
                    days=min(7, 2 ** min(empty_runs - 1, 3)),
                )).isoformat(timespec="seconds"),
            }
            if checkpoint:
                checkpoint(store, keyword, {"new": 0, "refreshed": 0, "duplicates": 0})
            print("  -> empty/end; cooled down without guessing exhaustion")
            break
        products = result["products"]
        identities = sorted(set(
            product.get("product_id") or product.get("source_url") or product["product_name"].lower()
            for product in products
        ))
        signature = hashlib.sha256(json.dumps(identities).encode()).hexdigest()
        if signature == state.get("page_signature"):
            crawl_state[state_key] = {
                **state, "last_page": 0, "page_signature": "",
                "retry_after": (datetime.now() + timedelta(days=1)).isoformat(timespec="seconds"),
            }
            if checkpoint:
                checkpoint(store, keyword, {"new": 0, "refreshed": 0, "duplicates": len(products)})
            print("  -> repeated page; wrapping cursor and cooling down")
            break
        counts = merge_products(
            products, store, standard_prices, daily_specials, category_fallback, seen_products,
        )
        found += len(products)
        last_page = page if store in PAGINATED_STORES else 1
        has_ended = result.get("has_more") is False
        crawl_state[state_key] = {
            "last_page": 0 if has_ended else last_page,
            "last_run": datetime.now().isoformat(timespec="seconds"),
            "page_signature": "" if has_ended else signature, "empty_runs": 0,
            "retry_after": (datetime.now() + timedelta(days=1)).isoformat(timespec="seconds")
            if has_ended else "",
        }
        if checkpoint:
            checkpoint(store, keyword, counts)
        print(f"  -> new={counts['new']} refreshed={counts['refreshed']} duplicates={counts['duplicates']}")
        if has_ended:
            break
    return found


def backfill() -> None:
    secrets = load_secrets()
    scraper = PriceScraper(secrets.get("APIFY_TOKEN", ""), secrets.get("ZENROWS_KEY", ""))
    sheets_manager = build_sheets_manager(secrets)
    budget = setup_maintenance_scraper(sheets_manager, scraper, "bulk_category_crawl")

    standard_prices = sheets_manager.load_standard_prices()
    daily_specials = sheets_manager.load_daily_specials()
    crawl_state = sheets_manager.load_crawl_state()

    totals = {"new": 0, "refreshed": 0, "duplicates": 0}

    def checkpoint(store, keyword, counts):
        if not sheets_manager.save_standard_prices(standard_prices):
            raise RuntimeError("Catalogue checkpoint failed; crawl cursor not saved")
        if not sheets_manager.save_daily_specials(daily_specials):
            raise RuntimeError("Specials checkpoint failed; crawl cursor not saved")
        if not sheets_manager.save_crawl_state(crawl_state):
            raise RuntimeError("Crawl checkpoint failed")
        budget.record_products(store, keyword, counts)
        for name in totals:
            totals[name] += counts[name]

    plans = {}
    for store in STORES_TO_CRAWL:
        targets = (
            get_coles_catalog_targets(crawl_state)
            if store == "Coles"
            else EXPANDED_CATEGORY_TARGETS if expansion_enabled() else CATEGORY_TARGETS
        )
        if store != "Coles" and expansion_enabled():
            cursor = crawl_state.get((store, "__catalog_cursor__"), {}).get("last_page", 0)
            targets = [
                targets[(cursor + offset) % len(targets)]
                for offset in range(EXPANDED_TARGETS_PER_RUN)
            ]
        plans[store] = targets
    total_scraped = 0
    seen_products = set()
    failures = []
    for target_index in range(max(len(targets) for targets in plans.values())):
        for store, targets in plans.items():
            if target_index >= len(targets):
                continue
            keyword, category_fallback = targets[target_index]
            try:
                total_scraped += crawl_keyword(
                    scraper, store, keyword, crawl_state, standard_prices,
                    daily_specials, category_fallback, checkpoint, seen_products,
                )
            except BudgetExceeded as error:
                print(f"Stopped at budget limit: {error}")
                refresh_performance_dashboard(sheets_manager.sh)
                if failures:
                    raise RuntimeError(f"Partial crawl: {len(failures)} listing failures")
                return
            except ProviderBlocked:
                raise
            except CrawlPageError as error:
                print(error)
                failures.append(str(error))
            if expansion_enabled() or store == "Coles":
                current = crawl_state.get((store, "__catalog_cursor__"), {}).get("last_page", 0)
                target_count = len(EXPANDED_COLES_TARGETS if store == "Coles" else EXPANDED_CATEGORY_TARGETS)
                if not expansion_enabled():
                    target_count = len(COLES_CATALOG_TARGETS)
                crawl_state[(store, "__catalog_cursor__")] = {
                    "last_page": (current + 1) % target_count,
                    "last_run": datetime.now().isoformat(timespec="seconds"),
                }
                if not sheets_manager.save_crawl_state(crawl_state):
                    raise RuntimeError("Search rotation checkpoint failed")

    prices_saved = sheets_manager.save_standard_prices(standard_prices)
    specials_saved = sheets_manager.save_daily_specials(daily_specials)
    state_saved = sheets_manager.save_crawl_state(crawl_state)

    for store in STORES_TO_CRAWL:
        store_count = sum(1 for (s, _) in standard_prices if s == store)
        sheets_manager.log_catalog_size(store, store_count)

    if prices_saved and specials_saved and state_saved:
        refresh_performance_dashboard(sheets_manager.sh)
    else:
        raise RuntimeError("Catalogue data was not fully saved; dashboard refresh skipped.")
    if failures:
        raise RuntimeError(f"Partial crawl: {len(failures)} listing failures; see job logs")

    print(
        f"\nDone. {total_scraped} product scrapes; new={totals['new']}, "
        f"refreshed={totals['refreshed']}, duplicates={totals['duplicates']}. "
        f"{len(standard_prices)} standard entries, {len(daily_specials)} active specials, "
        f"{len(crawl_state)} crawl cursors saved."
    )


if __name__ == "__main__":
    backfill()
