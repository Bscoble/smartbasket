import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.pricing import PriceScraper
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
