import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.pricing import PriceScraper
from config import APIFY_DEFAULT_CONFIG
from helpers import build_store_search_candidates, build_store_search_query


def test_iter_apify_products_flattens_result_payloads():
    scraper = PriceScraper(apify_token="token", zenrows_key="key")

    items = [{
        "results": [{
            "name": "Milk",
            "pricing": {"now": 4.99},
        }]
    }]

    assert scraper._iter_apify_products(items) == [{
        "name": "Milk",
        "pricing": {"now": 4.99},
    }]


def test_extract_apify_price_supports_nested_price_shapes():
    scraper = PriceScraper(apify_token="token", zenrows_key="key")

    assert scraper._extract_apify_price({"price": {"amount": "4.99"}}) == 4.99
    assert scraper._extract_apify_price({"offers": [{"price": "$6.50"}]}) == 6.5


def test_apify_search_returns_multiple_results_for_relevance_matching():
    assert APIFY_DEFAULT_CONFIG["max_items_per_url"] > 1


def test_build_store_search_query_keeps_brand_and_size():
    query = build_store_search_query("Arnotts Tim Tam Original 250g", "Woolworths")
    assert "Tim Tam" in query
    assert "250g" in query
    assert "Arnotts" in query or "Arnott's" in query
    assert "  " not in query


def test_build_store_search_candidates_returns_alternative_queries():
    candidates = build_store_search_candidates("Arnotts Tim Tam Original 250g", "Woolworths")
    assert len(candidates) >= 2
    assert any("Tim Tam" in candidate for candidate in candidates)
    assert any("250g" in candidate for candidate in candidates)
    assert any("Arnott" in candidate or "Arnotts" in candidate for candidate in candidates)


def test_brand_alias_expansion_uses_common_variants():
    candidates = build_store_search_candidates("Arnotts Tim Tam Original 250g", "Woolworths")
    assert any("Arnott's" in candidate for candidate in candidates)


def test_extract_bulk_product_info_keeps_category_metadata():
    scraper = PriceScraper(apify_token="token", zenrows_key="key")

    product = {
        "name": "Milk",
        "size": "2L",
        "brand": "Coles",
        "category": "dairy",
        "sub_category": "fresh milk",
        "pricing": {"now": "4.50", "was": "5.00"},
        "image_uris": [{"uri": "/test.png"}],
    }

    result = scraper._extract_bulk_product_info("Coles", product)

    assert result is not None
    assert result["category"] == "dairy"
    assert result["subcategory"] == "fresh milk"
    assert result["brand"] == "Coles"


def test_split_shopping_available_requires_multiple_cheapest_stores():
    from app import split_shopping_available

    single_store_report = {
        "item_breakdown": [{"cheapest_store": "Woolworths"}, {"cheapest_store": "Woolworths"}]
    }
    multi_store_report = {
        "item_breakdown": [{"cheapest_store": "Woolworths"}, {"cheapest_store": "Coles"}]
    }

    assert split_shopping_available(single_store_report) is False
    assert split_shopping_available(multi_store_report) is True


def test_save_product_keeps_category_metadata():
    class FakeWorksheet:
        def __init__(self):
            self.values = []

        def get_all_values(self):
            return [row[:] for row in self.values]

        def append_row(self, row):
            self.values.append(list(row))

        def update(self, range_name, values):
            if range_name == "A1:G1":
                self.values[0] = list(values[0])
            elif range_name.startswith("A") and ":" in range_name:
                start = range_name.split(":")[0]
                row_index = int(start[1:])
                self.values[row_index - 1] = list(values[0])
            else:
                self.values.append(list(values[0]))

    class FakeSpreadsheet:
        def __init__(self):
            self._sheets = {}

        def worksheet(self, name):
            if name not in self._sheets:
                self._sheets[name] = FakeWorksheet()
            return self._sheets[name]

        def add_worksheet(self, title, rows, cols):
            ws = FakeWorksheet()
            self._sheets[title] = ws
            return ws

    mgr = __import__("modules.sheets", fromlist=["SheetsManager"]).SheetsManager(FakeSpreadsheet())

    assert mgr.save_product("user@example.com", "Milk 2L", "https://img.test/milk.png", category="dairy", subcategory="fresh milk")

    saved = mgr.search_saved_products("user@example.com", "milk")
    assert saved[0]["category"] == "dairy"
    assert saved[0]["subcategory"] == "fresh milk"


def test_search_scraped_products_uses_stored_product_images():
    class FakeWorksheet:
        def __init__(self):
            self.values = [
                ["Store", "Item", "Price", "Product Name", "Last Verified", "Unit Price", "Unit Label", "Image URL", "Category", "Subcategory"],
                ["Woolworths", "Milk 2L", "4.90", "Milk 2L", "2026-08-21 12:00:00", "2.45", "per L", "https://scraped.test/milk.png", "dairy", "fresh milk"],
            ]

        def get_all_values(self):
            return [row[:] for row in self.values]

    class FakeSpreadsheet:
        def __init__(self):
            self._sheets = {"Standard Prices": FakeWorksheet()}

        def worksheet(self, name):
            if name not in self._sheets:
                self._sheets[name] = FakeWorksheet()
            return self._sheets[name]

        def add_worksheet(self, title, rows, cols):
            self._sheets[title] = FakeWorksheet()
            return self._sheets[title]

    mgr = __import__("modules.sheets", fromlist=["SheetsManager"]).SheetsManager(FakeSpreadsheet())

    results = mgr.search_scraped_products("milk")
    assert results[0]["title"] == "Milk 2L"
    assert results[0]["image_url"] == "https://scraped.test/milk.png"
    assert results[0]["category"] == "dairy"


def test_search_scraped_products_uses_images_from_legacy_standard_price_rows():
    class FakeWorksheet:
        def get_all_values(self):
            return [
                ["Store", "Item", "Price", "Product Name", "Last Verified", "Unit Price", "Unit Label", "Image URL"],
                ["Woolworths", "woolworths iced fruit loaf each", "4.50", "Woolworths Iced Fruit Loaf each", "2026-08-21 06:29:19", "0.81", "100G", "https://scraped.test/fruit-loaf.png"],
            ]

    class FakeSpreadsheet:
        def worksheet(self, name):
            assert name == "Standard Prices"
            return FakeWorksheet()

    mgr = __import__("modules.sheets", fromlist=["SheetsManager"]).SheetsManager(FakeSpreadsheet())

    results = mgr.search_scraped_products("woolworths iced fruit loaf")
    assert results == [{
        "title": "woolworths iced fruit loaf each",
        "image_url": "https://scraped.test/fruit-loaf.png",
        "category": "",
        "subcategory": "",
    }]
