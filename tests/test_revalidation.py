import os
import sys
from datetime import datetime, timedelta

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

        def load_standard_prices(self):
            return {("Coles", "tim tam"): entry}

        def save_standard_prices(self, prices):
            self.saved = prices

    class FakeScraper:
        def get_live_price_result(self, _store, _item, max_search_candidates):
            assert max_search_candidates == 1
            return {
                "price": 4.5,
                "product_name": "Arnott's Tim Tam Original",
                "brand": "Arnott's",
                "brand_source": "name_inference",
                "brand_confidence": "medium",
            }

    sheets_manager = FakeSheetsManager()
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

    revalidation_job.revalidate_stale_prices()

    saved = sheets_manager.saved[("Coles", "tim tam")]
    assert saved["price"] == 4.5
    assert saved["brand_source"] == "retailer"
    assert saved["brand_confidence"] == "high"
