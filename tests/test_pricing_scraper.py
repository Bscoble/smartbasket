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


def test_live_price_result_allows_revalidation_to_limit_search_candidates(monkeypatch):
    scraper = PriceScraper(apify_token="token", zenrows_key="key")
    captured = {}

    def fake_apify_result(store, item_name, max_search_candidates):
        captured.update({
            "store": store,
            "item_name": item_name,
            "max_search_candidates": max_search_candidates,
        })
        return {"price": 4.50, "status": "ok"}

    monkeypatch.setattr(scraper, "_get_apify_price_result", fake_apify_result)

    result = scraper.get_live_price_result("Coles", "Milk 2L", max_search_candidates=1)

    assert result["price"] == 4.50
    assert captured == {
        "store": "Coles",
        "item_name": "Milk 2L",
        "max_search_candidates": 1,
    }


def test_log_zenrows_usage_uses_configured_per_request_cost():
    scraper = PriceScraper(
        apify_token="",
        zenrows_key="key",
        zenrows_cost_per_request_usd=0.015,
    )
    calls = []
    scraper.usage_logger = lambda **kwargs: calls.append(kwargs)

    scraper._log_zenrows_usage("Aldi", "milk", "SUCCEEDED", started_at=0, product_count=30)

    assert calls[0]["store"] == "Aldi"
    assert calls[0]["query"] == "milk"
    assert calls[0]["status"] == "SUCCEEDED"
    assert calls[0]["cost_usd"] == 0.015
    assert calls[0]["product_count"] == 30


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


def _build_aldi_nuxt_html(products: list) -> str:
    """
    Build a minimal HTML page embedding a script#__NUXT_DATA__ payload in the
    same flat "devalue" array format Aldi's real pages use: nested values are
    integer indices back into the same top-level array.
    """
    data = []

    def push(value):
        data.append(value)
        return len(data) - 1

    for p in products:
        categories = [push({"id": "1", "name": cat_name, "urlSlugText": cat_name.lower()}) for cat_name in p.get("categories", [])]
        categories_idx = push(categories)
        assets = [
            push({
                "url": asset_url,
                "maxWidth": 1500,
                "maxHeight": 1500,
                "mimeType": "image/*",
                "assetType": asset_type,
                "alt": None,
                "displayName": None,
            })
            for asset_type, asset_url in p.get("assets", [])
        ]
        assets_idx = push(assets)
        price_idx = push({
            "amount": p["amount"],
            "amountRelevant": p["amount"],
            "amountRelevantDisplay": f"${p['amount'] / 100:.2f}",
            "comparison": p.get("comparison"),
            "comparisonDisplay": p.get("comparison_display"),
            "wasPriceDisplay": p.get("was_display"),
        })
        push({
            "sku": p["sku"],
            "name": p["name"],
            "brandName": p.get("brand"),
            "price": price_idx,
            "categories": categories_idx,
            "assets": assets_idx,
        })

    import json as _json
    payload = _json.dumps(data)
    return f'<html><body><script id="__NUXT_DATA__" type="application/json">{payload}</script></body></html>'


def test_parse_aldi_nuxt_products_extracts_price_image_and_category():
    html = _build_aldi_nuxt_html([
        {
            "sku": "000000000000173130",
            "name": "Mega Roulette 45g",
            "brand": "HARIBO",
            "amount": 99,
            "comparison": 220,
            "comparison_display": "$2.20 per 100 g",
            "was_display": None,
            "categories": ["Pantry", "Confectionery"],
            "assets": [("FR01", "https://dm.apac.cms.aldi.cx/is/image/aldiprodapac/product/jpg/scaleWidth/{width}/abc123/{slug}")],
        }
    ])

    products = PriceScraper._parse_aldi_nuxt_products(html)

    assert len(products) == 1
    product = products[0]
    assert product["product_name"] == "Mega Roulette 45g"
    assert product["brand"] == "HARIBO"
    assert product["price"] == 0.99
    assert product["standard_price"] == 0.99
    assert product["is_special"] is False
    assert product["unit_price"] == 2.20
    assert product["unit_label"] == "100 g"
    assert product["category"] == "Pantry"
    assert product["subcategory"] == "Confectionery"
    assert product["image_url"] == "https://dm.apac.cms.aldi.cx/is/image/aldiprodapac/product/jpg/scaleWidth/300/abc123/"


def test_parse_aldi_nuxt_products_detects_special_pricing():
    html = _build_aldi_nuxt_html([
        {
            "sku": "000000000000173131",
            "name": "Choceur Milk Chocolate Block 200g",
            "brand": "Choceur",
            "amount": 349,
            "comparison": 175,
            "comparison_display": "$1.75 per 100 g",
            "was_display": "$4.49",
            "categories": ["Pantry", "Confectionery"],
            "assets": [("FR01", "https://dm.apac.cms.aldi.cx/is/image/aldiprodapac/product/jpg/scaleWidth/{width}/def456/{slug}")],
        }
    ])

    products = PriceScraper._parse_aldi_nuxt_products(html)

    assert products[0]["price"] == 3.49
    assert products[0]["standard_price"] == 4.49
    assert products[0]["is_special"] is True
