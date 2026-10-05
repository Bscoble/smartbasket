import copy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.maintenance import MaintenanceBudget, BudgetExceeded, ProviderBlocked
from modules.pricing import PriceScraper
from evaluate_coles_provider import evaluate_pages


class Worksheet:
    def __init__(self):
        self.rows = []
        self.fail = False

    def get_all_values(self):
        return copy.deepcopy(self.rows)

    def append_row(self, row):
        if self.fail:
            raise RuntimeError("Sheets unavailable")
        self.rows.append(list(row))

    def update(self, range_name, values):
        if self.fail:
            raise RuntimeError("Sheets unavailable")
        number = int(range_name.split(":")[0][1:])
        self.rows[number - 1] = list(values[0])


class Sheets:
    def __init__(self):
        self.worksheets = {}

    def _get_or_create_worksheet(self, name, **kwargs):
        return self.worksheets.setdefault(name, Worksheet())


@pytest.fixture(autouse=True)
def default_budget(monkeypatch):
    for name in (
        "SCRAPER_DAILY_BUDGET_USD", "SCRAPER_MAX_REQUESTS_PER_DAY",
        "APIFY_MAX_RUN_COST_USD", "ZENROWS_COST_PER_REQUEST_USD",
        "ZENROWS_MAX_REQUEST_COST_USD",
    ):
        monkeypatch.delenv(name, raising=False)


def test_daily_reservations_survive_job_restarts_and_unknown_costs():
    sheets = Sheets()
    first = MaintenanceBudget(sheets, "crawl")
    for _ in range(10):
        first.settle(first.reserve("Coles", "apify"))
    second = MaintenanceBudget(sheets, "refresh")
    for _ in range(10):
        second.reserve("Aldi", "zenrows")
    with pytest.raises(BudgetExceeded):
        second.reserve("Coles", "apify")
    assert len(second.ws.rows) == 21


def test_actual_costs_release_unused_reservations_and_request_cap(monkeypatch):
    monkeypatch.setenv("SCRAPER_MAX_REQUESTS_PER_DAY", "2")
    budget = MaintenanceBudget(Sheets(), "crawl")
    budget.settle(budget.reserve("Coles", "apify"), 0.25, "settled")
    budget.reserve("Coles", "apify")
    with pytest.raises(BudgetExceeded):
        budget.reserve("Coles", "apify")
    assert budget.ws.rows[1][5] == "0.25"


def test_known_zenrows_cost_and_unknown_planning_cap(monkeypatch):
    monkeypatch.setenv("ZENROWS_COST_PER_REQUEST_USD", "0.15")
    budget = MaintenanceBudget(Sheets(), "crawl")
    reservation = budget.reserve("Aldi", "zenrows")
    assert reservation[2] == 0.15
    budget.settle(reservation)
    assert budget.ws.rows[-1][5] == "0.15"


@pytest.mark.parametrize("value", ["nan", "inf", "-1", "0", "garbage"])
def test_invalid_configuration_stops_before_requests(monkeypatch, value):
    monkeypatch.setenv("SCRAPER_DAILY_BUDGET_USD", value)
    with pytest.raises(ValueError):
        MaintenanceBudget(Sheets(), "crawl")


def test_reservation_failure_prevents_paid_request(monkeypatch):
    budget = MaintenanceBudget(Sheets(), "crawl")
    budget.ws.fail = True
    scraper = PriceScraper("token", "key")
    scraper.maintenance_budget = budget
    monkeypatch.setattr("modules.pricing.requests.get", lambda *a, **kw: pytest.fail("Paid request"))
    with pytest.raises(ProviderBlocked, match="reservation"):
        scraper.get_bulk_products_result("Aldi", "https://example.test")


def test_provider_billing_error_blocks_followup_calls(monkeypatch):
    budget = MaintenanceBudget(Sheets(), "crawl")
    scraper = PriceScraper("token", "key")
    scraper.maintenance_budget = budget
    calls = []
    def fake_get(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status_code=402)
    monkeypatch.setattr("modules.pricing.requests.get", fake_get)
    with pytest.raises(ProviderBlocked, match="402"):
        scraper.get_bulk_products_result("Aldi", "https://example.test")
    with pytest.raises(ProviderBlocked):
        scraper.get_bulk_products_result("Aldi", "https://example.test")
    assert len(calls) == 1
    assert budget.ws.rows[-1][5] == ""


def test_bulk_empty_is_not_end_and_retry_calls_are_bounded(monkeypatch):
    scraper = PriceScraper("token", "key")
    calls = []
    def fake_get(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status_code=200, text="<html>Challenge</html>", raise_for_status=lambda: None)
    monkeypatch.setattr("modules.pricing.requests.get", fake_get)
    monkeypatch.setattr("modules.pricing.time.sleep", lambda _seconds: None)
    result = scraper.get_bulk_products_result("Aldi", "https://example.test")
    assert result["status"] == "empty"
    assert len(calls) == 3


def test_apify_budget_options_and_timeout_abort(monkeypatch):
    budget = MaintenanceBudget(Sheets(), "crawl")
    scraper = PriceScraper("token", "key")
    scraper.maintenance_budget = budget
    options = {}
    aborted = []
    def call(**kwargs):
        options.update(kwargs)
        return SimpleNamespace(id="run", status="RUNNING", usage_total_usd=0.2)
    client = SimpleNamespace(
        actor=lambda actor: SimpleNamespace(call=call),
        run=lambda run_id: SimpleNamespace(abort=lambda: aborted.append(run_id)),
    )
    scraper._call_actor(client, "candidate", "Coles", {"urls": ["url"]})
    assert float(options["max_total_charge_usd"]) == 1
    assert options["run_timeout"].total_seconds() == 240
    assert aborted == ["run"]
    assert budget.ws.rows[-1][5] == "0.2"


def trial_product(identity):
    return {
        "id": identity, "name": "Biscuits", "size": "200g",
        "productUrl": f"https://www.coles.com.au/product/{identity}",
        "pricing": {"now": 3, "unit": {"price": 1.5, "of_measure_quantity": 100, "of_measure_units": "g"}},
    }


def test_provider_trial_rejects_repeated_pages_and_missing_metadata():
    one = [trial_product(str(i)) for i in range(5)]
    assert not evaluate_pages([one, one])["passed"]
    two = [trial_product(str(i)) for i in range(5, 10)]
    assert evaluate_pages([one, two])["passed"]
    two[0].pop("productUrl")
    assert not evaluate_pages([one, two])["passed"]


def test_settlement_failure_keeps_reservation_and_blocks_followups():
    sheets = Sheets()
    budget = MaintenanceBudget(sheets, "crawl")
    reservation = budget.reserve("Coles", "apify")
    budget.ws.fail = True
    with pytest.raises(ProviderBlocked, match="settlement"):
        budget.settle(reservation, 0.2)
    assert budget.ws.rows[-1][6] == "reserved"
    assert budget.blocked


def test_explicit_pagination_end_requires_matching_page():
    item = {"pagination": {"page": 2, "hasNextPage": False}}
    assert PriceScraper._confirmed_listing_end([item], "https://example.test?page=2")
    assert not PriceScraper._confirmed_listing_end([item], "https://example.test?page=1")
    assert not PriceScraper._confirmed_listing_end([{"products": []}], "https://example.test?page=2")


def test_concurrent_reservations_do_not_exceed_daily_limit():
    from concurrent.futures import ThreadPoolExecutor
    budget = MaintenanceBudget(Sheets(), "refresh")
    def reserve(_index):
        try:
            budget.reserve("Coles", "apify")
            return True
        except BudgetExceeded:
            return False
    with ThreadPoolExecutor(max_workers=4) as executor:
        assert sum(executor.map(reserve, range(40))) == 20
    assert len(budget.rows) == 21


def test_aldi_revalidation_matches_payload_product_instead_of_first_page_price(monkeypatch):
    scraper = PriceScraper("token", "key")
    monkeypatch.setattr(
        scraper, "_zenrows_get",
        lambda *args: SimpleNamespace(
            text='<script id="__NUXT_DATA__">[]</script><span class="price">$99</span>',
            raise_for_status=lambda: None,
        ),
    )
    monkeypatch.setattr(scraper, "_parse_aldi_nuxt_products", lambda _html: [
        {"product_name": "Milk 1L", "price": 2},
        {"product_name": "Milk 2L", "price": 3.50},
    ])
    assert scraper.get_live_price_result("Aldi", "Milk 2L")["price"] == 3.50
    assert scraper.get_live_price_result("Aldi", "Milk 3L")["status"] == "not_found"
