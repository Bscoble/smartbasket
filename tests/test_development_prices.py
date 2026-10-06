import os
import sys
from datetime import datetime, timedelta

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config import STANDARD_PRICE_MAX_AGE_DAYS
from modules.revalidation import select_stale_standard_prices
from modules.sheets import SheetsManager


class Placeholder:
    def text(self, _value):
        pass

    def progress(self, _value):
        pass

    def empty(self):
        pass


class FakeSheets:
    def __init__(self):
        self.prices = {}
        self.cache = {}
        self.specials = {}
        self.validator = SheetsManager(None)

    def load_price_cache(self):
        return self.cache

    def load_daily_specials(self):
        return self.specials

    def load_standard_prices(self):
        return self.prices

    def is_standard_price_valid(self, entry):
        return self.validator.is_standard_price_valid(entry)

    def is_cache_valid(self, entry):
        return self.validator.is_cache_valid(entry)


@pytest.fixture
def comparison(monkeypatch):
    import app

    sheets = FakeSheets()
    monkeypatch.delenv("DEVELOPMENT_ALLOW_STALE_PRICES", raising=False)
    monkeypatch.setattr(app, "sheets_manager", sheets)
    monkeypatch.setattr(app.st, "progress", lambda _value: Placeholder())
    monkeypatch.setattr(app.st, "empty", Placeholder)
    monkeypatch.setattr(app, "enqueue_missing_products", lambda _items: True)
    return app, sheets


def shelf_price(price=4.50, age_days=30, name="Full Cream Milk 2L"):
    return {
        "price": price, "product_name": name,
        "last_verified": datetime.now() - timedelta(days=age_days),
    }


def compare(app, stores=("Coles",)):
    return app.generate_smart_basket_report(
        [["Full Cream Milk 2L", "2", "each", "", "2"]], list(stores),
    )


def test_stale_fallback_is_off_by_default(comparison):
    app, sheets = comparison
    sheets.prices[("Coles", "full cream milk 2l")] = shelf_price()

    assert not app.development_stale_prices_enabled()
    assert compare(app) is None


def test_development_flag_rejects_invalid_values(comparison, monkeypatch):
    app, _sheets = comparison
    monkeypatch.setenv("DEVELOPMENT_ALLOW_STALE_PRICES", "yes")
    with pytest.raises(ValueError, match="must be true or false"):
        app.development_stale_prices_enabled()


def test_stale_price_is_labelled_and_totals_use_pack_count(comparison, monkeypatch):
    app, sheets = comparison
    monkeypatch.setenv("DEVELOPMENT_ALLOW_STALE_PRICES", "true")
    entry = shelf_price()
    sheets.prices[("Coles", "full cream milk 2l")] = entry
    verified = entry["last_verified"]

    report = compare(app)

    assert report["has_estimates"]
    assert report["stale_fallback_enabled"]
    assert report["comparison_modes"]["single_store_best"]["total_cost"] == 9.0
    assert report["comparison_modes"]["split_store_optimal"]["total_cost"] == 9.0
    data = dict(report["item_breakdown"][0]["all_stores"])["Coles"]
    assert data["status"] == "stale"
    assert data["last_verified"] == verified
    assert data["message"] == f"Outdated price - last verified {verified:%Y-%m-%d}"
    assert entry["last_verified"] == verified
    assert not sheets.is_standard_price_valid(entry)

    selected = select_stale_standard_prices(
        sheets.prices, {"Coles": 1}, STANDARD_PRICE_MAX_AGE_DAYS,
    )
    assert selected == [("Coles", "full cream milk 2l", entry)]

    monkeypatch.setenv("DEVELOPMENT_ALLOW_STALE_PRICES", "false")
    assert compare(app) is None


def test_fresh_shelf_match_beats_better_matching_stale_price(comparison, monkeypatch):
    app, sheets = comparison
    monkeypatch.setenv("DEVELOPMENT_ALLOW_STALE_PRICES", "true")
    sheets.prices = {
        ("Coles", "full cream milk 2l"): shelf_price(2.0),
        ("Coles", "milk full cream 2 litre"): shelf_price(5.0, age_days=1),
    }

    report = compare(app)
    assert not report["has_estimates"]
    assert report["outdated_prices"] == []
    data = dict(report["item_breakdown"][0]["all_stores"])["Coles"]
    assert data["status"] == "standard"
    assert data["total_price"] == 10.0


def test_fresh_cache_is_preferred_over_stale_shelf_price(comparison, monkeypatch):
    app, sheets = comparison
    monkeypatch.setenv("DEVELOPMENT_ALLOW_STALE_PRICES", "true")
    sheets.prices[("Coles", "full cream milk 2l")] = shelf_price(2.0)
    sheets.cache[("Coles", "milk full cream 2l")] = {
        "price": 5.0, "timestamp": datetime.now(),
        "product_name": "Full Cream Milk 2L",
    }

    report = compare(app)
    assert not report["has_estimates"]
    assert dict(report["item_breakdown"][0]["all_stores"])["Coles"]["status"] == "cached"


@pytest.mark.parametrize("age_days,expected", [(13, "standard"), (14, "stale")])
def test_fourteen_day_boundary_is_preserved(comparison, monkeypatch, age_days, expected):
    app, sheets = comparison
    monkeypatch.setenv("DEVELOPMENT_ALLOW_STALE_PRICES", "true")
    sheets.prices[("Coles", "full cream milk 2l")] = shelf_price(age_days=age_days)

    report = compare(app)
    assert dict(report["item_breakdown"][0]["all_stores"])["Coles"]["status"] == expected


@pytest.mark.parametrize("price", [None, "4.50", False, 0, -1, 99.99, float("nan"), float("inf")])
def test_stale_fallback_rejects_invalid_prices(comparison, monkeypatch, price):
    app, sheets = comparison
    monkeypatch.setenv("DEVELOPMENT_ALLOW_STALE_PRICES", "true")
    sheets.prices[("Coles", "full cream milk 2l")] = shelf_price(price)
    assert compare(app) is None


def test_missing_date_does_not_become_a_development_price(comparison, monkeypatch):
    app, sheets = comparison
    monkeypatch.setenv("DEVELOPMENT_ALLOW_STALE_PRICES", "true")
    entry = shelf_price()
    del entry["last_verified"]
    sheets.prices[("Coles", "full cream milk 2l")] = entry
    assert compare(app) is None


def test_unselected_stale_prices_mark_savings_as_estimates(comparison, monkeypatch):
    app, sheets = comparison
    monkeypatch.setenv("DEVELOPMENT_ALLOW_STALE_PRICES", "true")
    sheets.prices = {
        ("Coles", "full cream milk 2l"): shelf_price(4.0, age_days=1),
        ("Aldi", "full cream milk 2l"): shelf_price(6.0),
    }

    report = compare(app)
    assert report["has_estimates"]
    assert report["price_selection_savings"]["amount"] == 4.0
    assert report["outdated_prices"][0]["store"] == "Aldi"
    assert dict(report["item_breakdown"][0]["all_stores"])["Coles"]["status"] == "standard"


def test_expired_specials_and_cache_remain_excluded(comparison, monkeypatch):
    app, sheets = comparison
    monkeypatch.setenv("DEVELOPMENT_ALLOW_STALE_PRICES", "true")
    sheets.prices[("Coles", "full cream milk 2l")] = shelf_price(4.0)
    sheets.cache[("Coles", "full cream milk 2l")] = {
        "price": 1.0, "timestamp": datetime.now() - timedelta(days=2),
    }

    class Worksheet:
        row_count = 2

        def get_all_values(self):
            return [
                ["Store", "Item", "Price", "Product Name", "Date"],
                ["Coles", "full cream milk 2l", "0.50", "Full Cream Milk 2L",
                 (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")],
            ]

    class Spreadsheet:
        def worksheet(self, _name):
            return Worksheet()

    sheets.specials = SheetsManager(Spreadsheet()).load_daily_specials()
    assert sheets.specials == {}
    report = compare(app)
    data = dict(report["item_breakdown"][0]["all_stores"])["Coles"]
    assert data["status"] == "stale"
    assert data["total_price"] == 8.0


def test_current_special_is_preferred_over_stale_shelf_price(comparison, monkeypatch):
    app, sheets = comparison
    monkeypatch.setenv("DEVELOPMENT_ALLOW_STALE_PRICES", "true")
    sheets.prices[("Coles", "full cream milk 2l")] = shelf_price(4.0)
    sheets.specials[("Coles", "full cream milk 2l")] = {"price": 2.0}

    report = compare(app)
    assert not report["has_estimates"]
    assert dict(report["item_breakdown"][0]["all_stores"])["Coles"]["status"] == "special"
