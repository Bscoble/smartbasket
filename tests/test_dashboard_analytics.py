import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dashboard_analytics import (
    aggregate_category_coverage,
    aggregate_catalog_size_over_time,
    aggregate_catalogue_cost_efficiency,
    aggregate_catalogue_freshness,
    aggregate_catalogue_metrics,
    aggregate_comparison_basket_coverage,
    aggregate_oldest_price_age_by_category,
    aggregate_scrape_cost_by_day,
    aggregate_scrape_issues_by_day,
    aggregate_scrape_requests_and_errors,
    aggregate_scraper_budget,
    aggregate_store_health,
    aggregate_shopping_activity_by_day,
    aggregate_signups_last_n_days,
    aggregate_shop_mode_split,
    aggregate_engagement_by_week,
)
from datetime import datetime


def test_aggregate_catalog_size_over_time_pivots_by_store_and_keeps_max_per_day():
    rows = [
        ["Date", "Store", "Product Count"],
        ["2026-08-20", "Woolworths", "100"],
        ["2026-08-20", "Coles", "80"],
        ["2026-08-20", "Woolworths", "120"],  # same-day rerun, should keep the max
        ["2026-08-21", "Woolworths", "130"],
        ["2026-08-21", "Aldi", "40"],
    ]

    table = aggregate_catalog_size_over_time(rows)

    assert table[0] == ["Date", "Aldi", "Coles", "Woolworths", "Total"]
    assert table[1] == ["2026-08-20", "", "80", "120", "200"]
    assert table[2] == ["2026-08-21", "40", "", "130", "170"]
    assert len(table) == 3
    assert all(row[0] != "Total" for row in table[1:])


def test_aggregate_category_coverage_counts_standard_and_specials_by_store():
    standard_rows = [
        ["Store", "Item", "Price", "Product Name", "Last Verified", "Unit Price", "Unit Label", "Image URL", "Category", "Subcategory"],
        ["Aldi", "milk 2l", "4.50", "Milk 2L", "2026-08-21 10:00:00", "", "", "", "Dairy", "Milk"],
        ["Aldi", "cheese 250g", "5.00", "Cheese 250g", "2026-08-21 10:00:00", "", "", "", "Dairy", "Cheese"],
        ["Coles", "milk 2l", "5.50", "Milk 2L", "2026-08-21 10:00:00", "", "", "", "Dairy", "Milk"],
        ["Woolworths", "biscuits 250g", "3.50", "Biscuits 250g", "2026-08-21 10:00:00", "", "", "", "Pantry", "Biscuits"],
    ]
    special_rows = [
        ["Store", "Item", "Price", "Product Name", "Date"],
        ["Aldi", "milk 2l", "4.00", "Milk 2L", "2026-08-21"],
        ["Coles", "milk 2l", "4.50", "Milk 2L", "2026-08-21"],
        ["Coles", "unmatched product", "2.00", "Unmatched Product", "2026-08-21"],
    ]

    table = aggregate_category_coverage(standard_rows, special_rows)

    assert table[0] == [
        "Category", "Aldi Standard", "Aldi Specials", "Coles Standard", "Coles Specials",
        "Woolworths Standard", "Woolworths Specials", "Total Standard", "Total Specials",
    ]
    assert table[1] == ["Dairy", "2", "1", "1", "1", "0", "0", "3", "2"]
    assert table[2] == ["Pantry", "0", "0", "0", "0", "1", "0", "1", "0"]
    assert table[3] == ["Uncategorised", "0", "0", "0", "1", "0", "0", "0", "1"]
    assert table[4] == ["Total", "2", "1", "1", "2", "1", "0", "4", "3"]


def test_aggregate_oldest_price_age_by_category_returns_max_age_per_store():
    now = datetime(2026, 8, 22, 12, 0, 0)
    standard_rows = [
        ["Store", "Item", "Price", "Product Name", "Last Verified", "Unit Price", "Unit Label", "Image URL", "Category", "Subcategory"],
        ["Aldi", "milk", "4.50", "Milk", "2026-08-19 11:00:00", "", "", "", "Dairy", "Milk"],
        ["Aldi", "cheese", "5.00", "Cheese", "2026-08-16 12:00:00", "", "", "", "Dairy", "Cheese"],
        ["Coles", "milk", "5.50", "Milk", "2026-08-20 12:00:00", "", "", "", "Dairy", "Milk"],
        ["Woolworths", "pasta", "2.50", "Pasta", "2026-08-08 12:00:00", "", "", "", "Pantry", "Pasta"],
    ]

    table = aggregate_oldest_price_age_by_category(standard_rows, now=now)

    assert table[0] == ["Category", "Aldi", "Coles", "Woolworths", "Overall Oldest"]
    assert table[1] == ["Dairy", "6", "2", "", "6"]
    assert table[2] == ["Pantry", "", "", "14", "14"]


def test_aggregate_scrape_cost_by_day_sums_per_store():
    rows = [
        ["Timestamp", "Source", "Store", "Query", "Status", "Duration Secs", "Cost USD"],
        ["2026-08-20T04:00:00", "cache_warmer", "Woolworths", "milk", "ok", "10", "0.05"],
        ["2026-08-20T04:01:00", "cache_warmer", "Woolworths", "bread", "ok", "12", "0.03"],
        ["2026-08-20T04:02:00", "cache_warmer", "Coles", "milk", "ok", "9", "0.04"],
        ["2026-08-20T04:03:00", "cache_warmer", "Aldi", "milk", "ok", "8", ""],
    ]

    table = aggregate_scrape_cost_by_day(rows)

    assert table[0] == ["Date", "Aldi", "Coles", "Woolworths", "Total"]
    assert table[1] == ["2026-08-20", "", "0.0400", "0.0800", "0.1200"]
    assert table[2] == ["Total", "", "0.0400", "0.0800", "0.1200"]


def test_aggregate_scrape_issues_by_day_counts_non_ok_statuses():
    rows = [
        ["Timestamp", "Source", "Store", "Query", "Status", "Duration Secs", "Cost USD"],
        ["2026-08-20T04:00:00", "live_app", "Woolworths", "milk", "ok", "5", "0.01"],
        ["2026-08-20T04:01:00", "live_app", "Coles", "milk", "timeout", "90", ""],
        ["2026-08-21T04:00:00", "live_app", "Aldi", "milk", "not_found", "3", ""],
    ]

    table = aggregate_scrape_issues_by_day(rows)

    assert table == [
        ["Date", "Total Runs", "Issues"],
        ["2026-08-20", "2", "1"],
        ["2026-08-21", "1", "1"],
    ]


def test_aggregate_store_health_computes_issue_rate():
    rows = [
        ["Timestamp", "Source", "Store", "Query", "Status", "Duration Secs", "Cost USD"],
        ["2026-08-20T04:00:00", "live_app", "Coles", "milk", "ok", "5", "0.01"],
        ["2026-08-20T04:01:00", "live_app", "Coles", "bread", "timeout", "90", ""],
        ["2026-08-20T04:02:00", "live_app", "Coles", "eggs", "timeout", "90", ""],
    ]

    table = aggregate_store_health(rows)

    assert table == [
        ["Store", "Total Runs", "Issues", "Issue Rate %"],
        ["Coles", "3", "2", "66.7"],
    ]


def test_aggregate_shopping_activity_by_day_combines_events():
    rows = [
        ["Timestamp", "User ID", "Event Type", "Mode", "Items Ticked", "Items Total", "Savings"],
        ["2026-08-20T09:00:00", "a@x.com", "item_added", "direct", "", "", ""],
        ["2026-08-20T09:01:00", "a@x.com", "item_added", "direct", "", "", ""],
        ["2026-08-20T18:00:00", "a@x.com", "shop_completed", "single", "3", "4", "2.50"],
        ["2026-08-20T19:00:00", "b@x.com", "shop_completed", "split", "5", "5", "1.50"],
    ]

    table = aggregate_shopping_activity_by_day(rows)

    assert table == [
        ["Date", "Items Added", "Items Ticked", "Avg Savings"],
        ["2026-08-20", "2", "8", "2.0"],
    ]


def test_aggregate_signups_last_n_days_fills_missing_dates_with_zero():
    rows = [
        ["Email", "First Name", "Surname", "Postcode", "Country", "Password Hash", "Session Token Hash", "Token Created", "Created At"],
        ["a@x.com", "A", "A", "2000", "Australia", "h", "", "", "2026-08-19T10:00:00"],
        ["b@x.com", "B", "B", "2000", "Australia", "h", "", "", "2026-08-19T11:00:00"],
        ["c@x.com", "C", "C", "2000", "Australia", "h", "", "", "2026-08-15T11:00:00"],  # outside window
    ]

    table = aggregate_signups_last_n_days(rows, days=3, today=datetime(2026, 8, 21))

    assert table == [
        ["Date", "New Signups"],
        ["2026-08-19", "2"],
        ["2026-08-20", "0"],
        ["2026-08-21", "0"],
    ]


def test_aggregate_shop_mode_split_counts_single_vs_split():
    rows = [
        ["Timestamp", "User ID", "Event Type", "Mode", "Items Ticked", "Items Total", "Savings"],
        ["2026-08-20T09:00:00", "a@x.com", "shop_mode_selected", "single", "", "", ""],
        ["2026-08-20T09:05:00", "a@x.com", "shop_mode_selected", "split", "", "", ""],
        ["2026-08-20T09:06:00", "a@x.com", "shop_mode_selected", "split", "", "", ""],
    ]

    table = aggregate_shop_mode_split(rows)

    assert table == [
        ["Mode", "Count"],
        ["Single", "1"],
        ["Split", "2"],
    ]


def test_aggregate_engagement_by_week_groups_by_iso_week():
    rows = [
        ["Timestamp", "User ID", "Event Type", "Mode", "Items Ticked", "Items Total", "Savings"],
        ["2026-08-17T09:00:00", "a@x.com", "refer_click", "", "", "", ""],
        ["2026-08-18T09:00:00", "a@x.com", "contact_click", "", "", "", ""],
        ["2026-08-24T09:00:00", "b@x.com", "refer_click", "", "", "", ""],
    ]

    table = aggregate_engagement_by_week(rows)

    assert table[0] == ["Week", "Refer Clicks", "Contact Clicks"]
    assert table[1] == ["2026-W34", "1", "1"]
    assert table[2] == ["2026-W35", "1", "0"]


def test_aggregate_catalogue_metrics_groups_successful_checkpoint_counts():
    rows = [
        ["Timestamp", "Source", "Store", "Query", "New Products", "Refreshed Products", "Duplicate Products"],
        ["2026-10-01T10:00:00", "crawl", "Coles", "milk", "2", "3", "1"],
        ["2026-10-01T11:00:00", "crawl", "Coles", "bread", "4", "1", "2"],
        ["2026-10-02T10:00:00", "crawl", "Aldi", "milk", "1", "0", "0"],
        ["bad row"],
    ]

    assert aggregate_catalogue_metrics(rows) == [
        ["Date", "Source", "Store", "New Products", "Refreshed Products", "Duplicate Products"],
        ["2026-10-01", "crawl", "Coles", "6", "4", "3"],
        ["2026-10-02", "crawl", "Aldi", "1", "0", "0"],
    ]


def test_aggregate_scrape_requests_and_errors_counts_each_request():
    rows = [
        ["Timestamp", "Source", "Store", "Query", "Status", "Duration Secs", "Cost USD"],
        ["2026-10-01T10:00:00", "crawl", "Coles", "milk", "ok", "1", "0.10"],
        ["2026-10-01T10:01:00", "crawl", "Coles", "bread", "timeout", "2", ""],
        ["2026-10-01T10:02:00", "app", "Aldi", "milk", "SUCCEEDED", "1", "0.05"],
    ]

    assert aggregate_scrape_requests_and_errors(rows) == [
        ["Date", "Source", "Store", "Requests", "Errors"],
        ["2026-10-01", "app", "Aldi", "1", "0"],
        ["2026-10-01", "crawl", "Coles", "2", "1"],
    ]


def test_aggregate_catalogue_cost_efficiency_leaves_unknown_cost_ratios_blank():
    catalogue_rows = [
        ["Timestamp", "Source", "Store", "Query", "New Products", "Refreshed Products", "Duplicate Products"],
        ["2026-10-01T10:00:00", "crawl", "Coles", "milk", "2", "4", "1"],
        ["2026-10-02T10:00:00", "crawl", "Aldi", "milk", "3", "0", "0"],
    ]
    scrape_rows = [
        ["Timestamp", "Source", "Store", "Query", "Status", "Duration Secs", "Cost USD"],
        ["2026-10-01T10:00:00", "crawl", "Coles", "milk", "ok", "1", "0.1200"],
        ["2026-10-01T10:01:00", "crawl", "Coles", "bread", "timeout", "2", ""],
        ["2026-10-02T10:00:00", "crawl", "Aldi", "milk", "ok", "1", ""],
    ]

    table = aggregate_catalogue_cost_efficiency(catalogue_rows, scrape_rows)
    assert table[0] == [
        "Date", "Source", "Store", "New Products", "Refreshed Products",
        "Known Requests", "Unpriced Requests", "Recorded Cost USD",
        "Recorded USD / New Product", "Recorded USD / Refreshed Product",
    ]
    assert table[1] == [
        "2026-10-01", "crawl", "Coles", "2", "4", "1", "1", "0.1200", "", "",
    ]
    assert table[2] == ["2026-10-02", "crawl", "Aldi", "3", "0", "0", "1", "", "", ""]


def test_aggregate_scraper_budget_separates_reservation_caps_from_actual_cost():
    rows = [
        ["Timestamp", "Reservation ID", "Source", "Store", "Reserved USD", "Actual USD", "Status"],
        ["2026-10-01T10:00:00", "a", "crawl", "Coles", "1.00", "", "reserved"],
        ["2026-10-01T10:01:00", "b", "crawl", "Coles", "0.50", "0.12", "settled"],
        ["2026-10-01T10:02:00", "c", "crawl", "Coles", "0.25", "", "blocked"],
    ]
    assert aggregate_scraper_budget(rows) == [
        [
            "Date", "Source", "Store", "Status", "Requests",
            "Reserved USD (Planning Cap)", "Actual USD (Known)", "Actual USD Missing",
        ],
        ["2026-10-01", "crawl", "Coles", "reserved", "1", "1.0000", "", "1"],
        ["2026-10-01", "crawl", "Coles", "settled", "1", "0.5000", "0.1200", "0"],
        ["2026-10-01", "crawl", "Coles", "blocked", "1", "0.2500", "", "1"],
    ]


def test_cost_efficiency_uses_ledger_without_double_counting_logged_charges():
    catalogue = [
        ["Timestamp", "Source", "Store", "Query", "New Products", "Refreshed Products", "Duplicate Products"],
        ["2026-10-05T10:00:00", "crawl", "Coles", "milk", "2", "4", "0"],
    ]
    logs = [
        ["Timestamp", "Source", "Store", "Query", "Status", "Duration Secs", "Cost USD"],
        ["2026-10-05T10:00:00", "crawl", "Coles", "milk", "ok", "1", "0.10"],
    ]
    ledger = [
        ["Timestamp", "Reservation ID", "Source", "Store", "Reserved USD", "Actual USD", "Status"],
        ["2026-10-05T10:00:00", "r1", "crawl", "Coles", "1", "0.10", "settled"],
    ]
    assert aggregate_catalogue_cost_efficiency(catalogue, logs, ledger)[1][-3:] == [
        "0.1000", "0.0500", "0.0250",
    ]
    ledger.append(["2026-10-05T10:02:00", "r2", "crawl", "Coles", "1", "", "reserved"])
    assert aggregate_catalogue_cost_efficiency(catalogue, logs, ledger)[1][-3:] == [
        "0.1000", "", "",
    ]

def test_aggregate_catalogue_freshness_counts_distinct_store_product_keys():
    now = datetime(2026, 10, 15, 12, 0, 0)
    rows = [
        ["Store", "Item", "Price", "Product Name", "Last Verified"],
        ["Coles", " Milk ", "4.00", "Milk", "2026-10-15 10:00:00"],
        ["Coles", "milk", "4.10", "Milk", "2026-10-01 12:00:00"],  # duplicate key, latest date wins
        ["Coles", "Bread", "3.00", "Bread", "2026-10-01 12:00:00"],  # exactly 14 days is stale
        ["Coles", "Eggs", "5.00", "Eggs", ""],
        ["Aldi", "Milk", "4.00", "Milk", "2026-10-14 12:00:00"],
    ]

    assert aggregate_catalogue_freshness(rows, now=now) == [
        ["Store", "Current (<14 Days)", "Stale (14+ Days)", "Unknown Freshness", "Distinct Products"],
        ["Aldi", "1", "0", "0", "1"],
        ["Coles", "1", "1", "1", "3"],
        ["Total", "2", "1", "1", "4"],
    ]


def test_aggregate_comparison_basket_coverage_omits_user_ids_and_unknown_matches():
    rows = [
        ["Timestamp", "User ID", "Event Type", "Mode", "Items Ticked", "Items Total", "Savings"],
        ["2026-10-01T10:00:00", "private@example.com", "comparison_run", "", "3", "4", ""],
        ["2026-10-01T11:00:00", "another@example.com", "comparison_run", "", "", "2", ""],
        ["2026-10-01T12:00:00", "private@example.com", "shop_completed", "", "10", "10", ""],
    ]

    assert aggregate_comparison_basket_coverage(rows) == [
        ["Date", "Comparison Runs", "Measured Runs", "Matched Items", "Items Total", "Match Coverage %"],
        ["2026-10-01", "2", "1", "3", "4", "75.0"],
    ]
