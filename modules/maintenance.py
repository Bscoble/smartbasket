"""Persistent spend reservations for serialized catalogue-maintenance jobs."""

import math
import os
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from config import WORKSHEET_NAMES, WORKSHEET_CONFIG


class BudgetExceeded(RuntimeError):
    """The daily planning budget or request cap has been reached."""


class ProviderBlocked(RuntimeError):
    """A provider rejected further paid requests."""


def positive_setting(name: str, default: float) -> float:
    value = float(os.environ.get(name, str(default)))
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return value


class MaintenanceBudget:
    """Reserve costs before requests; unknown/unfinished charges retain reservations."""

    HEADERS = [
        "Timestamp", "Reservation ID", "Source", "Store", "Reserved USD",
        "Actual USD", "Status",
    ]

    def __init__(self, sheets_manager, source: str):
        self.sheets = sheets_manager
        self.source = source
        self.limit = positive_setting("SCRAPER_DAILY_BUDGET_USD", 20)
        self.max_requests = int(positive_setting("SCRAPER_MAX_REQUESTS_PER_DAY", 120))
        if self.max_requests < 1:
            raise ValueError("SCRAPER_MAX_REQUESTS_PER_DAY must be at least 1")
        self.apify_cap = positive_setting("APIFY_MAX_RUN_COST_USD", 1)
        self.zenrows_reserve = positive_setting("ZENROWS_MAX_REQUEST_COST_USD", 1)
        configured_cost = os.environ.get("ZENROWS_COST_PER_REQUEST_USD", "").strip()
        self.zenrows_cost = (
            positive_setting("ZENROWS_COST_PER_REQUEST_USD", 1) if configured_cost else None
        )
        self.lock = threading.RLock()
        self.blocked = False
        self.metrics_ws = None
        self.ws = self._worksheet("scraper_budget", self.HEADERS)
        self.rows = self.ws.get_all_values()
        if self.rows[0] != self.HEADERS:
            raise ProviderBlocked("Unexpected Scraper Budget headers; refusing paid requests")

    def _worksheet(self, key, headers):
        ws = self.sheets._get_or_create_worksheet(
            WORKSHEET_NAMES[key], **WORKSHEET_CONFIG[key],
        )
        if not ws.get_all_values():
            ws.append_row(headers)
        return ws

    def reserve(self, store: str, provider: str):
        with self.lock:
            if self.blocked:
                raise ProviderBlocked("Scraper budget stopped after a provider/billing error")
            today = datetime.now(timezone.utc).date().isoformat()
            spent = 0.0
            requests = 0
            for row in self.rows[1:]:
                if len(row) != len(self.HEADERS):
                    raise ProviderBlocked("Malformed spend ledger; refusing paid requests")
                if row[0][:10] != today:
                    continue
                try:
                    reserved = float(row[4])
                    actual = float(row[5]) if row[5] else None
                except ValueError as error:
                    raise ProviderBlocked("Invalid daily spend values; refusing paid requests") from error
                if (
                    not math.isfinite(reserved) or reserved <= 0
                    or (actual is not None and (not math.isfinite(actual) or actual < 0))
                ):
                    raise ProviderBlocked("Invalid daily spend values; refusing paid requests")
                spent += actual if actual is not None else reserved
                requests += 1
            amount = self.apify_cap if provider == "apify" else (
                self.zenrows_cost or self.zenrows_reserve
            )
            if requests >= self.max_requests or spent + amount > self.limit + 1e-9:
                raise BudgetExceeded(
                    f"Daily budget/request limit reached: ${spent:.2f}/${self.limit:.2f}, "
                    f"{requests}/{self.max_requests} requests"
                )
            row = [
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                uuid.uuid4().hex, self.source, store, str(amount), "", "reserved",
            ]
            try:
                self.ws.append_row(row)
            except Exception as error:
                self.blocked = True
                raise ProviderBlocked("Spend reservation could not be saved; stopped") from error
            self.rows.append(row)
            return len(self.rows), provider, amount

    def settle(self, reservation, actual=None, status="unknown"):
        with self.lock:
            row_number, provider, reserved = reservation
            if provider == "zenrows":
                actual = self.zenrows_cost
            if actual is not None:
                actual = float(actual)
                if not math.isfinite(actual) or actual < 0:
                    raise ValueError("Provider returned an invalid request charge")
            row = self.rows[row_number - 1][:]
            row[5] = str(actual) if actual is not None else ""
            row[6] = status if actual is not None or status != "settled" else "unknown"
            try:
                self.ws.update(f"A{row_number}:G{row_number}", [row])
            except Exception as error:
                self.blocked = True
                raise ProviderBlocked("Spend settlement could not be saved; stopped") from error
            self.rows[row_number - 1] = row
            if actual is not None and actual > reserved:
                self.blocked = True
                raise ProviderBlocked("Provider exceeded its reserved request cost; stopped")

    def record_products(self, store: str, query: str, counts: dict):
        headers = [
            "Timestamp", "Source", "Store", "Query", "New Products",
            "Refreshed Products", "Duplicate Products",
        ]
        with self.lock:
            if self.metrics_ws is None:
                self.metrics_ws = self._worksheet("catalogue_metrics", headers)
            self.metrics_ws.append_row([
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                self.source, store, query,
                counts["new"], counts["refreshed"], counts["duplicates"],
            ])


def setup_maintenance_scraper(sheets_manager, scraper, source: str) -> MaintenanceBudget:
    sheets_manager.strict_reads = True
    budget = MaintenanceBudget(sheets_manager, source)
    scraper.maintenance_budget = budget
    scraper.usage_logger = lambda **kwargs: sheets_manager.log_scrape_run(
        source=source, **kwargs,
    )
    return budget


def apify_call_options(budget):
    if budget is None:
        return {}
    return {"max_total_charge_usd": Decimal(str(budget.apify_cap))}
