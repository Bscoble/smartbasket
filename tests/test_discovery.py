import copy
import os
import sys
from datetime import datetime, timedelta

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.discovery import (
    DiscoveryQueue, QUEUE_HEADERS, missing_catalogue_requests, normalize_query,
)
from discover_requested_products import discover_requested_products
from modules.maintenance import BudgetExceeded, ProviderBlocked


NOW = datetime(2026, 10, 5, 12)


class Worksheet:
    def __init__(self):
        self.values = []
        self.fail = False

    def get_all_values(self):
        return copy.deepcopy(self.values)

    def append_row(self, row, **kwargs):
        self.append_rows([row], **kwargs)

    def append_rows(self, rows, **kwargs):
        if self.fail:
            raise RuntimeError("Sheets unavailable")
        assert kwargs.get("value_input_option") == "RAW"
        self.values.extend(copy.deepcopy(rows))

    def update(self, range_name, values, **kwargs):
        if self.fail:
            raise RuntimeError("Sheets unavailable")
        index = int(range_name.split(":")[0][1:]) - 1
        self.values[index] = copy.deepcopy(values[0])


class Sheets:
    def __init__(self):
        self.ws = Worksheet()
        self.standard = {}
        self.specials = {}
        self.names = []
        self.events = []
        self.fail_save = False

    def _get_or_create_worksheet(self, name, **kwargs):
        assert name == "Discovery Queue"
        assert kwargs["cols"] == "8"
        return self.ws

    def load_standard_prices(self):
        return copy.deepcopy(self.standard)

    def load_daily_specials(self):
        return copy.deepcopy(self.specials)

    def get_active_shopping_item_names(self):
        return self.names

    def is_standard_price_valid(self, entry):
        return bool(
            entry.get("last_verified")
            and NOW - entry["last_verified"] < timedelta(days=14)
        )

    def save_standard_prices(self, prices):
        self.events.append("standard checkpoint")
        if self.fail_save:
            return False
        self.standard = copy.deepcopy(prices)
        return True

    def save_daily_specials(self, prices):
        self.events.append("specials checkpoint")
        self.specials = copy.deepcopy(prices)
        return True


class Scraper:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def get_bulk_products_result(self, store, url, max_items):
        self.calls.append((store, url, max_items))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class Budget:
    def __init__(self):
        self.metrics = []

    def record_products(self, store, query, counts):
        self.metrics.append((store, query, counts))


def product(name):
    return {
        "product_name": name, "price": 3, "standard_price": 4,
        "is_special": True, "image_url": "image", "unit_price": 0.8,
        "unit_label": "100g", "source_url": "product-url", "brand": "Brand",
    }


def test_queue_headers_dedupe_store_scope_and_no_customer_fields():
    sheets = Sheets()
    queue = DiscoveryQueue(sheets)
    assert queue.enqueue([" Milk 2L ", "milk 2l", "Coles Bread"], now=NOW) == 4
    assert sheets.ws.values[0] == QUEUE_HEADERS
    assert all(len(row) == 8 for row in sheets.ws.values)
    assert [(row["store"], row["query"]) for row in queue.load()] == [
        ("Woolworths", "Milk 2L"), ("Coles", "Milk 2L"), ("Aldi", "Milk 2L"),
        ("Coles", "Coles Bread"),
    ]
    assert queue.enqueue(["MILK 2L", "coles bread"], now=NOW) == 0
    assert not any("user" in header.lower() or "email" in header.lower()
                   for header in QUEUE_HEADERS)
    assert queue.enqueue(["customer@example.com", ["Bread", "customer-id"],
                          {"name": "Bread", "email": "customer@example.com"}]) == 0
    assert normalize_query("https://example.com/account") == ""


def test_queue_persistence_failures_propagate():
    sheets = Sheets()
    sheets.ws.fail = True
    with pytest.raises(RuntimeError, match="Sheets unavailable"):
        DiscoveryQueue(sheets).enqueue(["Bread"])


def test_queue_cooldown_and_attempt_limit():
    sheets = Sheets()
    queue = DiscoveryQueue(sheets)
    queue.enqueue(["Bread"], stores=["Coles"], now=NOW)
    request = queue.due(NOW)[0]
    queue.update(request, attempts=1, status="retry", last_attempt=NOW.isoformat(),
                 next_attempt=(NOW + timedelta(days=1)).isoformat())
    assert queue.due(NOW + timedelta(hours=23)) == []
    assert len(queue.due(NOW + timedelta(days=1))) == 1
    queue.update(request, attempts=3)
    assert queue.due(NOW + timedelta(days=2)) == []
    assert queue.load()[0]["status"] == "exhausted"


def test_missing_selection_excludes_existing_fresh_and_stale_per_store():
    prices = {
        ("Coles", "milk 2l"): {"product_name": "Milk 2L", "last_verified": NOW},
        ("Aldi", "milk 2l"): {
            "product_name": "Milk 2L", "last_verified": NOW - timedelta(days=30),
        },
    }
    assert missing_catalogue_requests(["Milk 2L", "milk 2l"], prices) == {
        "Woolworths": ["Milk 2L"],
    }
    assert missing_catalogue_requests(["Coles Milk 2L"], prices) == {}
    assert missing_catalogue_requests(["Coles Milk 1L"], prices) == {
        "Coles": ["Coles Milk 1L"],
    }


def test_worker_upserts_all_products_checkpoints_then_completes():
    sheets = Sheets()
    queue = DiscoveryQueue(sheets)
    queue.enqueue(["Coles Milk 2L"], now=NOW)
    scraper = Scraper({"status": "ok", "products": [
        product("Milk 2L"), {**product("Bread"), "source_url": "bread-url"},
        product("Milk 2L"),
    ]})
    budget = Budget()
    original_update = queue.update

    def verified_update(request, **changes):
        if changes.get("status") == "complete":
            assert sheets.events == ["standard checkpoint", "specials checkpoint"]
        original_update(request, **changes)

    # All queue instances share the same persisted worksheet.
    from unittest.mock import patch
    with patch("discover_requested_products.DiscoveryQueue", return_value=queue):
        queue.update = verified_update
        summary = discover_requested_products(sheets, scraper, budget, now=NOW)
    assert summary == {"attempted": 1, "completed": 1, "failed": 0, "stopped": False}
    assert len(sheets.standard) == 2
    assert sheets.standard[("Coles", "milk 2l")]["unit_price"] == 0.8
    assert sheets.specials[("Coles", "milk 2l")]["price"] == 3
    assert budget.metrics[0][2] == {"new": 2, "refreshed": 0, "duplicates": 1}
    assert scraper.calls == [(
        "Coles", "https://www.coles.com.au/search/products?q=Coles%20Milk%202L", 20,
    )]
    assert queue.load()[0]["status"] == "complete"


@pytest.mark.parametrize("result", [
    {"status": "empty", "products": [], "message": "No products"},
    {"status": "error", "products": [], "message": "Provider failed"},
    {"status": "end", "products": []},
    {"status": "ok", "products": [product("Milk 1L")]},
    RuntimeError("Network failed"),
    [],
])
def test_worker_failures_retry_without_false_exact_inference(result):
    sheets = Sheets()
    queue = DiscoveryQueue(sheets)
    queue.enqueue(["Coles Milk 2L"], now=NOW)
    summary = discover_requested_products(sheets, Scraper(result), Budget(), now=NOW)
    request = queue.load()[0]
    assert summary["failed"] == 1
    assert request["attempts"] == 1
    assert request["status"] == "retry"
    assert request["last_error"]
    assert queue.due(NOW) == []
    assert ("Coles", "coles milk 2l") not in sheets.standard


@pytest.mark.parametrize("error", [BudgetExceeded("Budget full"), ProviderBlocked("Blocked")])
def test_worker_budget_refusal_stops_without_attempt_increment(error):
    sheets = Sheets()
    queue = DiscoveryQueue(sheets)
    queue.enqueue(["Coles Bread", "Coles Milk"], now=NOW)
    scraper = Scraper(error)
    summary = discover_requested_products(sheets, scraper, Budget(), now=NOW)
    assert summary["stopped"]
    assert summary["attempted"] == 0
    assert len(scraper.calls) == 1
    assert [row["attempts"] for row in queue.load()] == [0, 0]
    assert queue.load()[0]["last_error"] == str(error)


def test_worker_checkpoint_failure_never_completes():
    sheets = Sheets()
    sheets.fail_save = True
    queue = DiscoveryQueue(sheets)
    queue.enqueue(["Coles Bread"], now=NOW)
    summary = discover_requested_products(
        sheets, Scraper({"status": "ok", "products": [product("Bread")]}),
        Budget(), now=NOW,
    )
    assert summary["failed"] == 1
    assert queue.load()[0]["status"] == "retry"
    assert "checkpoint failed" in queue.load()[0]["last_error"]
    assert sheets.standard == {}


def test_worker_existing_matches_route_stale_to_revalidation_without_calls():
    sheets = Sheets()
    sheets.standard = {
        ("Coles", "bread"): {"product_name": "Bread", "last_verified": NOW},
        ("Aldi", "bread"): {
            "product_name": "Bread", "last_verified": NOW - timedelta(days=30),
        },
    }
    queue = DiscoveryQueue(sheets)
    queue.enqueue(["Bread"], stores=["Coles", "Aldi"], now=NOW)
    scraper = Scraper(RuntimeError("must not call"))
    discover_requested_products(sheets, scraper, Budget(), now=NOW)
    assert not scraper.calls
    assert [row["status"] for row in queue.load()] == ["complete", "revalidation"]
    assert [row["attempts"] for row in queue.load()] == [0, 0]


def test_worker_seeds_only_missing_active_names_and_respects_batch():
    sheets = Sheets()
    sheets.names = ["Coles Bread", "coles bread", "Aldi Milk", "Coles Eggs"]
    scraper = Scraper({"status": "empty", "products": []})
    summary = discover_requested_products(sheets, scraper, Budget(), batch_size=1, now=NOW)
    assert summary["attempted"] == 1
    assert len(scraper.calls) == 1
    assert len(DiscoveryQueue(sheets).load()) == 3


def test_worker_exhausts_after_three_calls():
    sheets = Sheets()
    queue = DiscoveryQueue(sheets)
    queue.enqueue(["Coles Bread"], now=NOW)
    scraper = Scraper({"status": "empty", "products": []})
    for day in range(4):
        discover_requested_products(
            sheets, scraper, Budget(), now=NOW + timedelta(days=day),
        )
    assert len(scraper.calls) == 3
    assert queue.load()[0]["status"] == "exhausted"


def test_app_queue_feedback_and_no_customer_data(monkeypatch):
    import app

    sheets = Sheets()
    messages = []
    monkeypatch.setattr(app, "sheets_manager", sheets)
    monkeypatch.setattr(app.st, "info", lambda message: messages.append(message))
    monkeypatch.setattr(app.st, "warning", lambda message: messages.append(message))
    monkeypatch.setattr(app, "price_scraper", object())
    assert app.enqueue_missing_products(["Coles Bread"])
    assert "offline" in messages[-1]
    assert DiscoveryQueue(sheets).load()[0]["query"] == "Coles Bread"
    sheets.ws.fail = True
    assert not app.enqueue_missing_products(["Coles Milk"])
    assert "Could not queue" in messages[-1]


def test_app_unpriced_comparison_queues_even_without_report(monkeypatch):
    import app

    class ComparisonSheets(Sheets):
        def load_price_cache(self):
            return {}

        def is_cache_valid(self, entry):
            return False

    class Placeholder:
        def progress(self, value):
            pass

        def text(self, value):
            pass

        def empty(self):
            pass

    sheets = ComparisonSheets()
    monkeypatch.setattr(app, "sheets_manager", sheets)
    monkeypatch.setattr(app.st, "info", lambda message: None)
    monkeypatch.setattr(app.st, "progress", lambda value: Placeholder())
    monkeypatch.setattr(app.st, "empty", Placeholder)
    report = app.generate_smart_basket_report(
        [["Coles Milk 2L", "1", "ea", "", "1"]], ["Coles"],
    )
    assert report is None
    assert [(item["store"], item["query"]) for item in DiscoveryQueue(sheets).load()] == [
        ("Coles", "Coles Milk 2L"),
    ]
