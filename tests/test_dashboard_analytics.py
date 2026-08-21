import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dashboard_analytics import (
    aggregate_catalog_size_over_time,
    aggregate_scrape_cost_by_day,
    aggregate_scrape_issues_by_day,
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


def test_aggregate_scrape_cost_by_day_sums_per_store():
    rows = [
        ["Timestamp", "Source", "Store", "Query", "Status", "Duration Secs", "Cost USD"],
        ["2026-08-20T04:00:00", "cache_warmer", "Woolworths", "milk", "ok", "10", "0.05"],
        ["2026-08-20T04:01:00", "cache_warmer", "Woolworths", "bread", "ok", "12", "0.03"],
        ["2026-08-20T04:02:00", "cache_warmer", "Coles", "milk", "ok", "9", "0.04"],
    ]

    table = aggregate_scrape_cost_by_day(rows)

    assert table[0] == ["Date", "Coles", "Woolworths"]
    assert table[1] == ["2026-08-20", "0.0400", "0.0800"]


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
