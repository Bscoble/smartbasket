import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from bulk_category_backfill import (
    CATEGORY_TARGETS,
    COLES_CATALOG_TARGETS,
    COLES_TARGETS_PER_RUN,
    crawl_keyword,
    get_coles_catalog_targets,
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
        def get_bulk_products(self, _store, _urls, max_items):
            assert max_items > 0
            return [{
                "product_name": "Choceur Milk Chocolate 200g",
                "price": 3.99,
                "standard_price": 3.99,
                "is_special": False,
                "brand": "Choceur",
                "brand_source": "retailer",
                "brand_confidence": "high",
            }]

    standard_prices = {}
    crawl_keyword(FakeScraper(), "Aldi", "chocolate", {}, standard_prices, {})

    saved = standard_prices[("Aldi", "choceur milk chocolate 200g")]
    assert saved["brand"] == "Choceur"
    assert saved["brand_source"] == "retailer"
    assert saved["brand_confidence"] == "high"
