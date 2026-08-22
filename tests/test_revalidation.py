import os
import sys
from datetime import datetime, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.revalidation import select_stale_standard_prices


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
