import os
import sys
from datetime import datetime, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.catalog_matching import find_local_price_matches


def _entry(product_name, price, age_days=0):
    return {
        "product_name": product_name,
        "price": price,
        "last_verified": datetime.now() - timedelta(days=age_days),
    }


def _is_fresh(entry):
    return datetime.now() - entry["last_verified"] < timedelta(days=14)


def test_local_price_matching_finds_name_variants_per_store():
    prices = {
        ("Coles", "arnotts tim tam double coat 200g"): _entry("Arnott's Tim Tam Double Coat 200g", 4.50),
        ("Woolworths", "arnotts tim tam double choc 200 g"): _entry("Arnott's Tim Tam Double Choc 200 g", 4.80),
        ("Aldi", "belmont chocolate biscuits 200g"): _entry("Belmont Chocolate Biscuits 200g", 3.20),
    }

    matches = find_local_price_matches(
        "Arnott's Tim Tam Double Coat 200g",
        ["Coles", "Woolworths", "Aldi"],
        prices,
        _is_fresh,
    )

    assert set(matches) == {"Coles", "Woolworths"}
    assert matches["Coles"][1]["price"] == 4.50
    assert matches["Woolworths"][1]["price"] == 4.80


def test_local_price_matching_rejects_wrong_size_and_stale_entries():
    prices = {
        ("Coles", "leg shaved ham 250g"): _entry("Leg Shaved Ham 250g", 4.99, age_days=15),
        ("Woolworths", "leg shaved ham 100g"): _entry("Leg Shaved Ham 100g", 3.99),
        ("Aldi", "beef rump steak 250g"): _entry("Beef Rump Steak 250g", 8.00),
    }

    matches = find_local_price_matches(
        "Shaved Ham 250g",
        ["Coles", "Woolworths", "Aldi"],
        prices,
        _is_fresh,
    )

    assert matches == {}


def test_local_price_matching_rejects_partial_short_names_and_wrong_pack_products():
    prices = {
        ("Aldi", "fruit rolls 6 pack 94g"): _entry("Fruit Rolls 6 Pack 94g", 3.99),
        ("Coles", "beef scotch steak fillet 2 pack 480g"): _entry(
            "Beef Scotch Steak Fillet 2 Pack 480g",
            18.00,
        ),
    }

    assert find_local_price_matches(
        "Scone Homestyle Fruit 6 Pack",
        ["Aldi"],
        prices,
        _is_fresh,
    ) == {}
    assert find_local_price_matches(
        "Beef Eye Fillet",
        ["Coles"],
        prices,
        _is_fresh,
    ) == {}


def test_basket_report_uses_local_matches_without_live_scraping(monkeypatch):
    import app

    class Placeholder:
        def text(self, _value):
            pass

        def progress(self, _value):
            pass

        def empty(self):
            pass

    class FakeSheets:
        def load_price_cache(self):
            return {}

        def load_daily_specials(self):
            return {}

        def load_standard_prices(self):
            return {
                ("Coles", "full cream milk 2l"): _entry("Coles Full Cream Milk 2L", 3.20),
                ("Woolworths", "beef rump steak 500g"): _entry("Beef Rump Steak 500g", 9.00),
            }

        def is_standard_price_valid(self, entry):
            return _is_fresh(entry)

    class NoLiveScraper:
        def get_live_price_result(self, *_args, **_kwargs):
            raise AssertionError("interactive comparison must not scrape retailers")

    monkeypatch.setattr(app, "sheets_manager", FakeSheets())
    monkeypatch.setattr(app, "price_scraper", NoLiveScraper())
    monkeypatch.setattr(app.st, "progress", lambda _value: Placeholder())
    monkeypatch.setattr(app.st, "empty", Placeholder)

    report = app.generate_smart_basket_report(
        [["Milk Full Cream 2L", "1", "each", "", "1"]],
        ["Coles", "Woolworths"],
    )

    assert report is not None
    assert report["store_rankings"][0]["store"] == "Coles"
    assert report["store_rankings"][0]["coverage_count"] == 1
    assert report["item_breakdown"][0]["cheapest_store"] == "Coles"