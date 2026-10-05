"""Manually invoked, budget-bounded discovery of requested missing products."""

import json
import os
from datetime import datetime
from urllib.parse import quote

from config import GOOGLE_SCOPES, SPREADSHEET_ID, STORES
from modules.catalog_matching import find_local_price_matches
from modules.maintenance import ProviderBlocked
from modules.discovery import (
    DiscoveryQueue, MAX_ATTEMPTS, RETRY_COOLDOWN, missing_catalogue_requests,
)


def build_dependencies():
    import gspread
    from google.oauth2.service_account import Credentials
    from modules.maintenance import setup_maintenance_scraper
    from modules.pricing import PriceScraper
    from modules.sheets import SheetsManager

    def secret(name):
        value = os.environ.get(name, "").strip()
        if not value:
            raise RuntimeError(f"Missing required environment variable: {name}")
        return value

    credentials = Credentials.from_service_account_info(
        json.loads(secret("GCP_SERVICE_ACCOUNT")), scopes=GOOGLE_SCOPES,
    )
    manager = SheetsManager(gspread.authorize(credentials).open_by_key(SPREADSHEET_ID))
    scraper = PriceScraper(secret("APIFY_TOKEN"), secret("ZENROWS_KEY"))
    budget = setup_maintenance_scraper(manager, scraper, "requested_discovery")
    return manager, scraper, budget


def discover_requested_products(sheets_manager, scraper, budget, batch_size=15, now=None):
    from modules.catalog_maintenance import merge_products
    from modules.maintenance import BudgetExceeded, ProviderBlocked

    now = now or datetime.now()
    queue = DiscoveryQueue(sheets_manager)
    standard_prices = sheets_manager.load_standard_prices()
    daily_specials = sheets_manager.load_daily_specials()
    missing = missing_catalogue_requests(
        sheets_manager.get_active_shopping_item_names(), standard_prices,
    )
    for store, queries in missing.items():
        queue.enqueue(queries, stores=[store], now=now)

    summary = {"attempted": 0, "completed": 0, "failed": 0, "stopped": False}
    seen_products = set()
    for request in queue.due(now, batch_size):
        store, query = request["store"], request["query"]
        existing = find_local_price_matches(
            query, [store], standard_prices, lambda entry: True,
        )
        if existing:
            entry = existing[store][1]
            fresh = sheets_manager.is_standard_price_valid(entry)
            queue.update(
                request, status="complete" if fresh else "revalidation",
                last_error="", next_attempt="",
            )
            continue

        attempts = request["attempts"]
        try:
            url = STORES[store]["search_url"].format(quote(query, safe=""))
            result = scraper.get_bulk_products_result(store, url, max_items=20)
            attempts += 1
            summary["attempted"] += 1
            if not isinstance(result, dict):
                raise RuntimeError("Invalid bulk-search result")
            products = result.get("products") or []
            counts = merge_products(
                products, store, standard_prices, daily_specials,
                seen_products=seen_products,
            )
            if products:
                if not sheets_manager.save_standard_prices(standard_prices):
                    raise RuntimeError("Standard Prices checkpoint failed")
                if not sheets_manager.save_daily_specials(daily_specials):
                    raise RuntimeError("Daily Specials checkpoint failed")
            budget.record_products(store, query, counts)
            matched = find_local_price_matches(
                query, [store], standard_prices, sheets_manager.is_standard_price_valid,
            )
            if result.get("status") == "ok" and matched:
                queue.update(
                    request, attempts=attempts, last_attempt=now.isoformat(),
                    status="complete", last_error="", next_attempt="",
                )
                summary["completed"] += 1
                print(f"{store}: {query}: saved catalogue match ({counts})")
                continue
            raise RuntimeError(
                result.get("message") or
                f"{result.get('status', 'error')}: no conservative catalogue match"
            )
        except (BudgetExceeded, ProviderBlocked) as error:
            # A refused call is not a product attempt; do not exhaust demand.
            queue.update(request, last_error=str(error)[:500])
            summary["stopped"] = True
            print(f"Discovery stopped: {error}")
            break
        except Exception as error:
            if attempts == request["attempts"]:
                attempts += 1
                summary["attempted"] += 1
            queue.update(
                request, attempts=attempts, last_attempt=now.isoformat(),
                status="exhausted" if attempts >= MAX_ATTEMPTS else "retry",
                last_error=str(error)[:500],
                next_attempt=(now + RETRY_COOLDOWN).isoformat()
                if attempts < MAX_ATTEMPTS else "",
            )
            summary["failed"] += 1
            print(f"{store}: {query}: discovery failed: {error}")
            # A failed checkpoint must never allow later in-memory matches to
            # claim completion of catalogue data that was not persisted.
            standard_prices = sheets_manager.load_standard_prices()
            daily_specials = sheets_manager.load_daily_specials()
            seen_products.clear()
    return summary


def main():
    manager, scraper, budget = build_dependencies()
    summary = discover_requested_products(manager, scraper, budget)
    print(f"Discovery summary: {summary}")
    if budget.blocked:
        raise ProviderBlocked("Discovery stopped after a provider or spend-ledger failure")
    if summary["failed"]:
        raise RuntimeError("Discovery encountered failures; see persisted queue errors")


if __name__ == "__main__":
    main()
