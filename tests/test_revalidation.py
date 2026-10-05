import os
import sys
from datetime import datetime, timedelta
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.revalidation import select_stale_standard_prices
import revalidate_stale_prices as revalidation_job


def test_select_stale_standard_prices_uses_oldest_entries_and_store_caps():
    now = datetime(2026, 8, 22, 9, 0, 0)
    standard_prices = {
        ("Woolworths", "old milk"): {"last_verified": now - timedelta(days=30)},
        ("Woolworths", "old bread"): {"last_verified": now - timedelta(days=20)},
        ("Woolworths", "fresh eggs"): {"last_verified": now - timedelta(days=5)},
        ("Coles", "unknown date"): {},
        ("Coles", "old pasta"): {"last_verified": now - timedelta(days=16)},
    }

    selected = select_stale_standard_prices(
        standard_prices,
        {"Woolworths": 1, "Coles": 2},
        max_age_days=14,
        now=now,
    )

    assert [(store, item) for store, item, _entry in selected] == [
        ("Woolworths", "old milk"),
        ("Coles", "unknown date"),
        ("Coles", "old pasta"),
    ]


def test_select_stale_standard_prices_excludes_entries_inside_freshness_window():
    now = datetime(2026, 8, 22, 9, 0, 0)
    standard_prices = {
        ("Aldi", "fresh product"): {"last_verified": now - timedelta(days=13, hours=23)},
    }

    selected = select_stale_standard_prices(
        standard_prices,
        {"Aldi": 10},
        max_age_days=14,
        now=now,
    )

    assert selected == []


def test_shopping_list_matches_take_priority_with_store_caps_and_oldest_fill():
    now = datetime(2026, 10, 5, 10)
    prices = {
        ("Coles", "old bread"): {
            "product_name": "Bread", "last_verified": now - timedelta(days=40),
        },
        ("Coles", "pork sausages 550g"): {
            "product_name": "Pork Sausages 550g",
            "last_verified": now - timedelta(days=18),
        },
        ("Coles", "milk 1l"): {
            "product_name": "Milk 1L", "last_verified": now - timedelta(days=50),
        },
        ("Aldi", "apple juice 2l"): {
            "product_name": "Apple Juice 2L",
            "last_verified": now - timedelta(days=20),
        },
    }
    selected = select_stale_standard_prices(
        prices, {"Coles": 2, "Aldi": 1}, 14, now,
        shopping_item_names=["Pork Sausage 550g", "Pork Sausage 550g", "Milk 2L"],
    )
    assert [(store, item) for store, item, _ in selected] == [
        ("Coles", "pork sausages 550g"),
        ("Coles", "milk 1l"),
        ("Aldi", "apple juice 2l"),
    ]


def test_shopping_priority_respects_store_specific_names_and_fresh_matches():
    now = datetime(2026, 10, 5, 10)
    prices = {
        ("Woolworths", "old bread"): {
            "product_name": "Bread", "last_verified": now - timedelta(days=40),
        },
        ("Woolworths", "mixed rainbow vegetables 750g"): {
            "product_name": "Mixed Rainbow Vegetables 750g",
            "last_verified": now - timedelta(days=20),
        },
        ("Coles", "old bread"): {
            "product_name": "Bread", "last_verified": now - timedelta(days=40),
        },
        ("Coles", "mixed rainbow vegetables 750g"): {
            "product_name": "Mixed Rainbow Vegetables 750g",
            "last_verified": now - timedelta(days=20),
        },
        ("Aldi", "old bread"): {
            "product_name": "Bread", "last_verified": now - timedelta(days=40),
        },
        ("Aldi", "milk 2l"): {
            "product_name": "Milk 2L", "last_verified": now - timedelta(days=20),
        },
        ("Aldi", "fresh milk 2l"): {
            "product_name": "Fresh Milk 2L", "last_verified": now,
        },
    }
    selected = select_stale_standard_prices(
        prices, {"Woolworths": 1, "Coles": 1, "Aldi": 1}, 14, now,
        shopping_item_names=[
            "Woolworths washed & ready to cook mixed rainbow vegetables 750g",
            "Milk 2L",
        ],
    )
    assert [(store, item) for store, item, _ in selected] == [
        ("Woolworths", "mixed rainbow vegetables 750g"),
        ("Coles", "old bread"),
        ("Aldi", "old bread"),
    ]


def test_shopping_priority_does_not_exceed_caps_and_successes_leave_queue():
    now = datetime(2026, 10, 5, 10)
    prices = {
        ("Coles", "milk 2l"): {
            "product_name": "Milk 2L", "last_verified": now - timedelta(days=30),
        },
        ("Coles", "apple juice 2l"): {
            "product_name": "Apple Juice 2L", "last_verified": now - timedelta(days=20),
        },
    }
    names = ["Milk 2L", "Apple Juice 2L"]
    first = select_stale_standard_prices(
        prices, {"Coles": 1}, 14, now, shopping_item_names=names,
    )
    assert [(store, item) for store, item, _ in first] == [("Coles", "milk 2l")]
    prices[("Coles", "milk 2l")]["last_verified"] = now
    second = select_stale_standard_prices(
        prices, {"Coles": 1}, 14, now, shopping_item_names=names,
    )
    assert [(store, item) for store, item, _ in second] == [("Coles", "apple juice 2l")]


def test_revalidation_preserves_stronger_existing_brand_metadata(monkeypatch):
    entry = {
        "price": 4.0,
        "product_name": "Arnott's Tim Tam",
        "last_verified": datetime.now() - timedelta(days=30),
        "brand": "Arnott's",
        "brand_source": "retailer",
        "brand_confidence": "high",
    }

    class FakeSheetsManager:
        saved = None
        sh = object()

        def load_standard_prices(self):
            return {
                ("Coles", "old milk"): {
                    "price": 3.0, "last_verified": datetime.now() - timedelta(days=40),
                },
                ("Coles", "tim tam"): entry,
            }

        def get_active_shopping_item_names(self):
            return ["Arnott's Tim Tam"]

        def save_standard_prices(self, prices):
            self.saved = prices
            return True

    class FakeScraper:
        def get_live_price_result(self, _store, _item, max_search_candidates):
            assert max_search_candidates == 1
            assert (_store, _item) == ("Coles", "tim tam")
            return {
                "price": 4.5,
                "product_name": "Arnott's Tim Tam Original",
                "brand": "Arnott's",
                "brand_source": "name_inference",
                "brand_confidence": "medium",
            }

    sheets_manager = FakeSheetsManager()
    refreshed = []
    monkeypatch.setattr(
        revalidation_job,
        "build_dependencies",
        lambda: (sheets_manager, FakeScraper()),
    )
    monkeypatch.setattr(
        revalidation_job,
        "STALE_REVALIDATION_BATCH_LIMITS",
        {"Coles": 1},
    )
    monkeypatch.setattr(
        revalidation_job,
        "refresh_performance_dashboard",
        lambda spreadsheet: refreshed.append(spreadsheet),
    )

    revalidation_job.revalidate_stale_prices()

    saved = sheets_manager.saved[("Coles", "tim tam")]
    assert saved["price"] == 4.5
    assert saved["brand_source"] == "retailer"
    assert saved["brand_confidence"] == "high"
    assert refreshed == [sheets_manager.sh]


@pytest.mark.parametrize("status", ["scraper_error", "connection", "not_found"])
def test_revalidation_fails_when_no_prices_are_refreshed(monkeypatch, status):
    class FakeSheetsManager:
        def get_active_shopping_item_names(self):
            return ["milk"]

        def load_standard_prices(self):
            return {("Coles", "milk"): {
                "price": 3.0,
                "last_verified": datetime.now() - timedelta(days=30),
            }}

        def save_standard_prices(self, _prices):
            raise AssertionError("Failed results must not overwrite existing prices")

    class FakeScraper:
        def get_live_price_result(self, _store, _item, max_search_candidates):
            return {"price": None, "status": status}

    monkeypatch.setattr(
        revalidation_job, "build_dependencies",
        lambda: (FakeSheetsManager(), FakeScraper()),
    )
    monkeypatch.setattr(revalidation_job, "STALE_REVALIDATION_BATCH_LIMITS", {"Coles": 1})

    with pytest.raises(RuntimeError, match="No stale prices were refreshed"):
        revalidation_job.revalidate_stale_prices()


def test_revalidation_succeeds_when_no_stale_prices_are_due(monkeypatch):
    class FakeSheetsManager:
        def get_active_shopping_item_names(self):
            return []

        def load_standard_prices(self):
            return {}

    monkeypatch.setattr(
        revalidation_job, "build_dependencies",
        lambda: (FakeSheetsManager(), object()),
    )

    revalidation_job.revalidate_stale_prices()


def test_revalidation_stops_if_shopping_lists_cannot_be_read(monkeypatch):
    class FakeSheetsManager:
        def load_standard_prices(self):
            return {}

        def get_active_shopping_item_names(self):
            raise RuntimeError("Shopping List unavailable")

    monkeypatch.setattr(
        revalidation_job, "build_dependencies",
        lambda: (FakeSheetsManager(), object()),
    )
    with pytest.raises(RuntimeError, match="Shopping List unavailable"):
        revalidation_job.revalidate_stale_prices()
