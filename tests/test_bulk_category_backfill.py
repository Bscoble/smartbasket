import os
import sys
from datetime import datetime
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from bulk_category_backfill import (
    CATEGORY_TARGETS,
    COLES_CATALOG_TARGETS,
    COLES_TARGETS_PER_RUN,
    crawl_keyword,
    get_coles_catalog_targets,
    EXPANDED_COLES_TARGETS_PER_RUN,
    CrawlPageError,
)


def test_category_targets_use_dashboard_category_labels():
    targets = dict(CATEGORY_TARGETS)

    assert targets["dairy"] == "Dairy, Eggs & Fridge"
    assert targets["household"] == "Cleaning & Household"
    assert targets["biscuits"] == "Snacks & Confectionery"
    assert {"baby", "pet food", "deli", "seafood"} <= set(targets)


def test_coles_catalog_targets_start_at_dairy_and_are_bounded():
    targets = get_coles_catalog_targets({})

    assert len(targets) == COLES_TARGETS_PER_RUN
    assert targets[0] == ("full cream milk", "Dairy")
    assert targets[-1] == ("crumpets", "Bakery")


def test_coles_catalog_targets_resume_from_saved_cursor_and_wrap():
    cursor = len(COLES_CATALOG_TARGETS) - 2
    state = {("Coles", "__catalog_cursor__"): {"last_page": cursor}}

    targets = get_coles_catalog_targets(state)

    assert targets[0] == COLES_CATALOG_TARGETS[-2]
    assert targets[1] == COLES_CATALOG_TARGETS[-1]
    assert targets[2] == COLES_CATALOG_TARGETS[0]


def test_crawl_keyword_persists_resolved_brand_metadata():
    class FakeScraper:
        def get_bulk_products_result(self, _store, _urls, max_items):
            assert max_items > 0
            return {"status": "ok", "products": [{
                "product_name": "Choceur Milk Chocolate 200g",
                "price": 3.99,
                "standard_price": 3.99,
                "is_special": False,
                "brand": "Choceur",
                "brand_source": "retailer",
                "brand_confidence": "high",
            }]}

    standard_prices = {}
    crawl_keyword(FakeScraper(), "Aldi", "chocolate", {}, standard_prices, {})

    saved = standard_prices[("Aldi", "choceur milk chocolate 200g")]
    assert saved["brand"] == "Choceur"
    assert saved["brand_source"] == "retailer"
    assert saved["brand_confidence"] == "high"


def test_expanded_coles_batch_requires_flag_and_wraps(monkeypatch):
    monkeypatch.setenv("EXPANDED_CATALOG_ENABLED", "true")
    assert len(get_coles_catalog_targets({})) == EXPANDED_COLES_TARGETS_PER_RUN == 25
    monkeypatch.setenv("EXPANDED_CATALOG_ENABLED", "false")
    assert len(get_coles_catalog_targets({})) == 10


class ListingScraper:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []

    def get_bulk_products_result(self, store, url, max_items):
        self.calls.append(url)
        return next(self.results)


def listing(name, identity):
    return {"status": "ok", "products": [{
        "product_name": name, "product_id": identity, "price": 3,
        "standard_price": 4, "is_special": True,
    }]}


def test_failed_page_keeps_last_successful_cursor_and_checkpoints():
    state = {}
    saved = []
    scraper = ListingScraper([
        listing("Milk 2L", "one"),
        {"status": "error", "message": "Retailer failed", "products": []},
    ])
    prices = {}
    with pytest.raises(CrawlPageError, match="Retailer failed"):
        crawl_keyword(
            scraper, "Woolworths", "milk", state, prices, {},
            checkpoint=lambda *args: saved.append(state[("Woolworths", "milk")].copy()),
        )
    assert len(saved) == 1
    assert state[("Woolworths", "milk")]["last_page"] == 1
    assert "pageNumber=2" in scraper.calls[-1]


def test_empty_page_does_not_skip_or_reset_cursor_and_has_cooldown():
    state = {("Aldi", "milk"): {"last_page": 4}}
    scraper = ListingScraper([{"status": "empty", "products": []}])
    crawl_keyword(scraper, "Aldi", "milk", state, {}, {})
    assert state[("Aldi", "milk")]["last_page"] == 4
    assert state[("Aldi", "milk")]["empty_runs"] == 1
    assert datetime.fromisoformat(state[("Aldi", "milk")]["retry_after"]) > datetime.now()
    crawl_keyword(scraper, "Aldi", "milk", state, {}, {})
    assert len(scraper.calls) == 1


def test_repeat_page_wraps_instead_of_advancing_indefinitely():
    state = {}
    counts = []
    scraper = ListingScraper([listing("Milk 2L", "one"), listing("Milk 2L", "one")])
    crawl_keyword(
        scraper, "Aldi", "milk", state, {}, {},
        checkpoint=lambda _store, _query, value: counts.append(value),
    )
    assert state[("Aldi", "milk")]["last_page"] == 0
    assert counts == [
        {"new": 1, "refreshed": 0, "duplicates": 0},
        {"new": 0, "refreshed": 0, "duplicates": 1},
    ]


def test_checkpoint_failure_stops_further_paid_calls():
    scraper = ListingScraper([listing("Milk 2L", "one")])
    def fail(*args):
        raise RuntimeError("Persistence failed")
    with pytest.raises(RuntimeError, match="Persistence failed"):
        crawl_keyword(scraper, "Aldi", "milk", {}, {}, {}, checkpoint=fail)
    assert len(scraper.calls) == 1


def test_confirmed_end_saves_last_page_products_before_wrapping():
    scraper = ListingScraper([{**listing("Milk 2L", "one"), "has_more": False}])
    state, prices = {}, {}
    assert crawl_keyword(scraper, "Woolworths", "milk", state, prices, {}) == 1
    assert ("Woolworths", "milk 2l") in prices
    assert state[("Woolworths", "milk")]["last_page"] == 0
    assert len(scraper.calls) == 1


def test_repeated_ambiguous_empty_visits_restart_without_skipping():
    state = {("Aldi", "milk"): {"last_page": 40, "empty_runs": 2}}
    scraper = ListingScraper([{"status": "empty", "products": []}])
    crawl_keyword(scraper, "Aldi", "milk", state, {}, {})
    assert state[("Aldi", "milk")]["last_page"] == 0
    assert state[("Aldi", "milk")]["empty_runs"] == 3


def test_merge_preserves_metadata_counts_unique_and_removes_old_special():
    from modules.catalog_maintenance import merge_products
    key = ("Coles", "milk 2l")
    prices = {key: {
        "price": 3, "source_url": "milk-url", "image_url": "image",
        "category": "Dairy", "barcode": "9300601433247",
    }}
    specials = {key: {"price": 2}}
    products = [{
        "product_name": "Coles Milk 2L", "source_url": "milk-url",
        "price": 4, "standard_price": 4, "is_special": False,
    }]
    seen = set()
    assert merge_products(products, "Coles", prices, specials, seen_products=seen) == {
        "new": 0, "refreshed": 1, "duplicates": 0,
    }
    assert len(prices) == 1
    assert prices[key]["image_url"] == "image"
    assert prices[key]["category"] == "Dairy"
    assert key not in specials
    assert merge_products(products, "Coles", prices, specials, seen_products=seen) == {
        "new": 0, "refreshed": 0, "duplicates": 1,
    }
