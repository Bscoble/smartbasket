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


def test_support_banner_uses_icon_and_escapes_text(monkeypatch):
    import app

    rendered = []
    monkeypatch.setattr(
        app.st, "markdown",
        lambda body, **kwargs: rendered.append((body, kwargs)),
    )

    app.render_support_banner("Contact <Support>", "Help & sharing")

    body, kwargs = rendered[0]
    assert f'src="{app.BRAND_MARK_DATA_URI}"' in body
    assert "Contact &lt;Support&gt;" in body
    assert "Help &amp; sharing" in body
    assert kwargs["unsafe_allow_html"] is True


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


def test_local_price_matching_handles_multipacks_and_plural_variants():
    prices = {
        ("Coles", "sprite lemonade 10 x 375ml"): _entry(
            "Sprite Lemonade Soft Drink 10 x 375mL",
            11.50,
        ),
        ("Woolworths", "uncle tobys protein vanilla quick oats 8 x 46g"): _entry(
            "Uncle Tobys Big Bowl Protein Vanilla Quick Oats 8 x 46g",
            6.75,
        ),
        ("Aldi", "pork sausages 550g"): _entry("Pork Sausages 550g", 5.99),
    }

    matches = find_local_price_matches(
        "Sprite Lemonade Soft Drink Cans 375ml x 10 Pack",
        ["Coles", "Woolworths", "Aldi"],
        prices,
        _is_fresh,
    )
    assert set(matches) == {"Coles"}

    matches = find_local_price_matches(
        "Uncle Tobys Big Bowl Quick Oats Protein Vanilla Porridge 368g x 8 Pack",
        ["Coles", "Woolworths", "Aldi"],
        prices,
        _is_fresh,
    )
    assert set(matches) == {"Woolworths"}

    matches = find_local_price_matches(
        "Pork Sausage 550g",
        ["Coles", "Woolworths", "Aldi"],
        prices,
        _is_fresh,
    )
    assert set(matches) == {"Aldi"}

    matches = find_local_price_matches(
        "Woolworths washed & ready to cook mixed rainbow vegetables 750g",
        ["Woolworths"],
        {
            ("Woolworths", "mixed rainbow vegetables 750g"): _entry(
                "Mixed Rainbow Vegetables 750g",
                3.50,
            ),
        },
        _is_fresh,
    )
    assert set(matches) == {"Woolworths"}

    matches = find_local_price_matches(
        "Olive Oil 1L",
        ["Coles"],
        {
            ("Coles", "olive oil 1000ml"): _entry("Olive Oil 1000mL", 8.00),
        },
        _is_fresh,
    )
    assert set(matches) == {"Coles"}


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


def test_basket_report_uses_fresh_cached_prices_for_name_variants(monkeypatch):
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
            return {
                ("Coles", "milk full cream 2l"): {
                    "price": 2.00,
                    "timestamp": datetime.now() - timedelta(hours=48),
                    "product_name": "Milk Full Cream 2L",
                },
                ("Coles", "full cream milk 2 litre"): {
                    "price": 3.20,
                    "timestamp": datetime.now(),
                    "product_name": "Full Cream Milk 2L",
                }
            }

        def load_daily_specials(self):
            return {}

        def load_standard_prices(self):
            return {}

        def is_standard_price_valid(self, _entry):
            return False

        def is_cache_valid(self, entry):
            timestamp = entry.get("timestamp")
            return timestamp is not None and datetime.now() - timestamp < timedelta(hours=24)

    monkeypatch.setattr(app, "sheets_manager", FakeSheets())
    monkeypatch.setattr(app.st, "progress", lambda _value: Placeholder())
    monkeypatch.setattr(app.st, "empty", Placeholder)

    report = app.generate_smart_basket_report(
        [["Milk Full Cream 2L", "1", "each", "", "1"]],
        ["Coles"],
    )

    assert report is not None
    assert report["unpriced_items"] == []
    assert report["item_breakdown"][0]["cheapest_store"] == "Coles"
    assert report["item_breakdown"][0]["total_price"] == "$3.20"


def test_basket_report_calculates_item_selection_savings(monkeypatch):
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
                ("Woolworths", "full cream milk 2l"): _entry("Woolworths Full Cream Milk 2L", 4.50),
            }

        def is_standard_price_valid(self, entry):
            return _is_fresh(entry)

    monkeypatch.setattr(app, "sheets_manager", FakeSheets())
    monkeypatch.setattr(app.st, "progress", lambda _value: Placeholder())
    monkeypatch.setattr(app.st, "empty", Placeholder)

    report = app.generate_smart_basket_report(
        [["Full Cream Milk 2L", "1", "each", "", "1"]],
        ["Coles", "Woolworths"],
    )

    assert report["price_selection_savings"] == {
        "amount": 1.30,
        "compared_items": 1,
    }
    assert round(report["item_breakdown"][0]["savings_vs_highest"], 2) == 1.30


def test_basket_savings_include_unselected_store_prices(monkeypatch):
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
                ("Woolworths", "full cream milk 2l"): _entry("Woolworths Full Cream Milk 2L", 4.50),
                ("Aldi", "full cream milk 2l"): _entry("Aldi Full Cream Milk 2L", 5.00),
            }

        def is_standard_price_valid(self, entry):
            return _is_fresh(entry)

        def is_cache_valid(self, _entry):
            return False

    monkeypatch.setattr(app, "sheets_manager", FakeSheets())
    monkeypatch.setattr(app.st, "progress", lambda _value: Placeholder())
    monkeypatch.setattr(app.st, "empty", Placeholder)

    report = app.generate_smart_basket_report(
        [["Full Cream Milk 2L", "1", "each", "", "1"]],
        ["Coles", "Woolworths"],
    )

    assert report["price_selection_savings"] == {
        "amount": 1.80,
        "compared_items": 1,
    }


def test_basket_report_matches_local_multipack_variants(monkeypatch):
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
                ("Coles", "sprite lemonade 10 x 375ml"): _entry(
                    "Sprite Lemonade Soft Drink 10 x 375mL",
                    11.50,
                ),
            }

        def is_standard_price_valid(self, entry):
            return _is_fresh(entry)

        def is_cache_valid(self, _entry):
            return False

    monkeypatch.setattr(app, "sheets_manager", FakeSheets())
    monkeypatch.setattr(app.st, "progress", lambda _value: Placeholder())
    monkeypatch.setattr(app.st, "empty", Placeholder)

    report = app.generate_smart_basket_report(
        [["Sprite Lemonade Soft Drink Cans 375ml x 10 Pack", "1", "each", "", "1"]],
        ["Coles"],
    )

    assert report is not None
    assert report["unpriced_items"] == []
    assert report["item_breakdown"][0]["cheapest_store"] == "Coles"