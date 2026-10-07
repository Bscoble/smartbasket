"""
Pure aggregation logic for the Performance Dashboard.

Kept separate from build_dashboard.py (which handles Sheets I/O and chart
creation) so the actual business logic - grouping/summing raw log rows into
the small tables the dashboard charts read from - can be unit tested without
a live spreadsheet.

Each function takes the raw worksheet rows (including the header row, as
returned by worksheet.get_all_values()) for one of the log sheets:
  - Catalog Size History: ["Date", "Store", "Product Count"]
  - Scrape Log: ["Timestamp", "Source", "Store", "Query", "Status", "Duration Secs", "Cost USD"]
  - User Events: ["Timestamp", "User ID", "Event Type", "Mode", "Items Ticked", "Items Total", "Savings"]
  - Users: [... "Created At"] (last column)
and returns a small table (list of header + rows) ready to write to the
dashboard sheet and chart.
"""

from collections import defaultdict
from datetime import datetime, timedelta
import math
from typing import List

from config import STANDARD_PRICE_MAX_AGE_DAYS


def _safe_float(value: str, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _date_part(timestamp: str) -> str:
    """Return the YYYY-MM-DD portion of an ISO timestamp or plain date string."""
    return (timestamp or "")[:10]


def _optional_nonnegative_float(value: str):
    """Parse a recorded USD value without turning missing or invalid data into zero."""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    return amount if math.isfinite(amount) and amount >= 0 else None


def aggregate_catalogue_metrics(rows: List[List[str]]) -> List[List[str]]:
    """Group successfully persisted catalogue checkpoint metrics by date, source, and store."""
    totals: dict = defaultdict(lambda: [0, 0, 0])
    for row in rows[1:] if rows else []:
        if len(row) < 7:
            continue
        timestamp, source, store = row[:3]
        if not timestamp or not store:
            continue
        key = (_date_part(timestamp), source, store)
        for index, value in enumerate(row[4:7]):
            try:
                totals[key][index] += max(0, int(value))
            except (TypeError, ValueError):
                continue

    table = [["Date", "Source", "Store", "New Products", "Refreshed Products", "Duplicate Products"]]
    for (date, source, store), counts in sorted(totals.items()):
        table.append([date, source, store, *(str(count) for count in counts)])
    return table


def aggregate_scrape_requests_and_errors(rows: List[List[str]]) -> List[List[str]]:
    """Count logged scraper requests and non-success statuses by date, source, and store."""
    counts: dict = defaultdict(lambda: [0, 0])
    for row in rows[1:] if rows else []:
        if len(row) < 5:
            continue
        timestamp, source, store, _query, status = row[:5]
        if not timestamp or not store:
            continue
        key = (_date_part(timestamp), source, store)
        counts[key][0] += 1
        if status.strip().lower() not in ("ok", "succeeded"):
            counts[key][1] += 1

    table = [["Date", "Source", "Store", "Requests", "Errors"]]
    for (date, source, store), (requests, errors) in sorted(counts.items()):
        table.append([date, source, store, str(requests), str(errors)])
    return table


def aggregate_catalogue_cost_efficiency(
    catalogue_rows: List[List[str]],
    scrape_rows: List[List[str]],
    budget_rows: List[List[str]] = None,
) -> List[List[str]]:
    """Compare recorded scrape spend with new/refreshed products, preserving unknown costs."""
    products: dict = defaultdict(lambda: [0, 0])
    for row in catalogue_rows[1:] if catalogue_rows else []:
        if len(row) < 7 or not row[0] or not row[2]:
            continue
        key = (_date_part(row[0]), row[1], row[2])
        for index, value in enumerate(row[4:6]):
            try:
                products[key][index] += max(0, int(value))
            except (TypeError, ValueError):
                continue

    if budget_rows and len(budget_rows) > 1:
        covered = {(row[0][:10], row[2], row[3]) for row in budget_rows[1:] if len(row) >= 7}
        scrape_rows = [scrape_rows[0] if scrape_rows else []] + [
            row for row in scrape_rows[1:]
            if len(row) >= 7 and (row[0][:10], row[1], row[2]) not in covered
        ] + [
            [row[0], row[2], row[3], "", row[6], "", row[5]]
            for row in budget_rows[1:] if len(row) >= 7
        ]
    spend: dict = defaultdict(lambda: [0.0, 0, 0])
    for row in scrape_rows[1:] if scrape_rows else []:
        if len(row) < 7 or not row[0] or not row[2]:
            continue
        key = (_date_part(row[0]), row[1], row[2])
        cost = _optional_nonnegative_float(row[6])
        if cost is None:
            spend[key][2] += 1
        else:
            spend[key][0] += cost
            spend[key][1] += 1

    table = [[
        "Date", "Source", "Store", "New Products", "Refreshed Products",
        "Known Requests", "Unpriced Requests", "Recorded Cost USD",
        "Recorded USD / New Product", "Recorded USD / Refreshed Product",
    ]]
    for key in sorted(set(products) | set(spend)):
        new_count, refreshed_count = products[key]
        recorded_cost, known_requests, unpriced_requests = spend[key]
        cost_text = f"{recorded_cost:.4f}" if known_requests else ""
        new_ratio = (
            f"{recorded_cost / new_count:.4f}"
            if known_requests and new_count and not unpriced_requests else ""
        )
        refreshed_ratio = (
            f"{recorded_cost / refreshed_count:.4f}"
            if known_requests and refreshed_count and not unpriced_requests else ""
        )
        table.append([
            *key,
            str(new_count),
            str(refreshed_count),
            str(known_requests),
            str(unpriced_requests),
            cost_text,
            new_ratio,
            refreshed_ratio,
        ])
    return table


def aggregate_scraper_budget(rows: List[List[str]]) -> List[List[str]]:
    """Summarize reservation caps separately from known and unknown actual spend."""
    totals: dict = defaultdict(lambda: [0, 0.0, 0.0, 0, 0])
    status_order = {"reserved": 0, "settled": 1, "error": 2, "blocked": 3, "unknown": 4}
    for row in rows[1:] if rows else []:
        if len(row) < 7 or not row[0] or not row[3]:
            continue
        timestamp, _reservation_id, source, store, reserved_text, actual_text, status_text = row[:7]
        status = status_text.strip().lower() or "unknown"
        key = (_date_part(timestamp), source, store, status)
        totals[key][0] += 1

        reserved = _optional_nonnegative_float(reserved_text)
        if reserved is not None:
            totals[key][1] += reserved

        actual = _optional_nonnegative_float(actual_text)
        if actual is None:
            totals[key][4] += 1
        else:
            totals[key][2] += actual
            totals[key][3] += 1

    table = [[
        "Date", "Source", "Store", "Status", "Requests",
        "Reserved USD (Planning Cap)", "Actual USD (Known)", "Actual USD Missing",
    ]]
    for key in sorted(
        totals,
        key=lambda item: (item[0], item[1], item[2], status_order.get(item[3], 5), item[3]),
    ):
        requests, reserved, actual, known_actual_count, missing_actual_count = totals[key]
        table.append([
            *key,
            str(requests),
            f"{reserved:.4f}",
            f"{actual:.4f}" if known_actual_count else "",
            str(missing_actual_count),
        ])
    return table


def aggregate_catalogue_freshness(
    standard_rows: List[List[str]],
    now: datetime = None,
    max_age_days: int = STANDARD_PRICE_MAX_AGE_DAYS,
) -> List[List[str]]:
    """Count distinct store/product keys by freshness, using the scraper's strict age limit."""
    now = now or datetime.now()
    latest_verified: dict = {}
    product_stores: dict = {}
    for row in standard_rows[1:] if standard_rows else []:
        if len(row) < 2 or not row[0].strip() or not row[1].strip():
            continue
        store = row[0].strip()
        key = (store.casefold(), row[1].strip().casefold())
        product_stores[key] = store
        verified = None
        if len(row) > 4 and row[4].strip():
            try:
                verified = datetime.fromisoformat(row[4].strip().replace("Z", "+00:00"))
                if verified.tzinfo is not None:
                    verified = verified.replace(tzinfo=None)
            except ValueError:
                pass
        if verified is not None and (key not in latest_verified or verified > latest_verified[key]):
            latest_verified[key] = verified

    counts: dict = defaultdict(lambda: [0, 0, 0])
    for key, store in product_stores.items():
        verified = latest_verified.get(key)
        if verified is None:
            counts[store][2] += 1
        elif now - verified < timedelta(days=max_age_days):
            counts[store][0] += 1
        else:
            counts[store][1] += 1

    table = [[
        "Store", f"Current (<{max_age_days} Days)", f"Stale ({max_age_days}+ Days)", "Unknown Freshness",
        "Distinct Products",
    ]]
    total_current = total_stale = total_unknown = 0
    for store in sorted(counts):
        current, stale, unknown = counts[store]
        total_current += current
        total_stale += stale
        total_unknown += unknown
        table.append([store, str(current), str(stale), str(unknown), str(current + stale + unknown)])
    if counts:
        table.append([
            "Total", str(total_current), str(total_stale), str(total_unknown),
            str(total_current + total_stale + total_unknown),
        ])
    return table


def aggregate_comparison_basket_coverage(rows: List[List[str]]) -> List[List[str]]:
    """Summarize matched versus requested items for comparison_run events without user identifiers."""
    totals: dict = defaultdict(lambda: {"runs": 0, "measured": 0, "matched": 0, "items": 0})
    for row in rows[1:] if rows else []:
        if len(row) < 7 or row[2] != "comparison_run" or not row[0]:
            continue
        date = _date_part(row[0])
        totals[date]["runs"] += 1
        try:
            matched = int(row[4]) if row[4].strip() else None
            items = int(row[5]) if row[5].strip() else None
        except (TypeError, ValueError):
            continue
        if matched is None or items is None or matched < 0 or items < 0:
            continue
        totals[date]["measured"] += 1
        totals[date]["matched"] += matched
        totals[date]["items"] += items

    table = [[
        "Date", "Comparison Runs", "Measured Runs", "Matched Items",
        "Items Total", "Match Coverage %",
    ]]
    for date, values in sorted(totals.items()):
        coverage = (
            f"{100 * values['matched'] / values['items']:.1f}"
            if values["measured"] and values["items"] else ""
        )
        table.append([
            date,
            str(values["runs"]),
            str(values["measured"]),
            str(values["matched"]) if values["measured"] else "",
            str(values["items"]) if values["measured"] else "",
            coverage,
        ])
    return table


def aggregate_catalog_size_over_time(rows: List[List[str]]) -> List[List[str]]:
    """Build a Date x Store product-count table for the catalog-size line chart."""
    data_rows = rows[1:] if rows else []
    stores = sorted({row[1] for row in data_rows if len(row) >= 3 and row[1]})
    by_date: dict = defaultdict(dict)

    for row in data_rows:
        if len(row) < 3:
            continue
        date, store, count_str = row[0], row[1], row[2]
        try:
            count = int(count_str)
        except (TypeError, ValueError):
            continue
        # A store can be crawled more than once a day; keep the largest count seen.
        by_date[date][store] = max(count, by_date[date].get(store, 0))

    table = [["Date"] + stores + ["Total"]]
    for date in sorted(by_date.keys()):
        store_counts = [by_date[date].get(store, 0) for store in stores]
        table.append([date] + [str(count) if count else "" for count in store_counts] + [str(sum(store_counts))])
    return table


def aggregate_category_coverage(standard_rows: List[List[str]], daily_special_rows: List[List[str]]) -> List[List[str]]:
    """Build current Standard Prices and Daily Specials counts by category and store."""
    standard_data = standard_rows[1:] if standard_rows else []
    specials_data = daily_special_rows[1:] if daily_special_rows else []
    stores = sorted({row[0].strip() for row in standard_data + specials_data if len(row) >= 2 and row[0].strip()})
    categories_by_product = {}
    counts: dict = defaultdict(lambda: defaultdict(lambda: {"standard": 0, "specials": 0}))

    for row in standard_data:
        if len(row) < 2 or not row[0].strip() or not row[1].strip():
            continue
        store = row[0].strip()
        item = row[1].strip().lower()
        category = row[8].strip() if len(row) >= 9 and row[8].strip() else "Uncategorised"
        categories_by_product[(store, item)] = category
        counts[category][store]["standard"] += 1

    for row in specials_data:
        if len(row) < 2 or not row[0].strip() or not row[1].strip():
            continue
        store = row[0].strip()
        item = row[1].strip().lower()
        category = categories_by_product.get((store, item), "Uncategorised")
        counts[category][store]["specials"] += 1

    headers = ["Category"]
    for store in stores:
        headers.extend([f"{store} Standard", f"{store} Specials"])
    headers.extend(["Total Standard", "Total Specials"])

    table = [headers]
    grand_standard = defaultdict(int)
    grand_specials = defaultdict(int)
    for category in sorted(counts.keys()):
        row = [category]
        total_standard = 0
        total_specials = 0
        for store in stores:
            standard_count = counts[category][store]["standard"]
            special_count = counts[category][store]["specials"]
            row.extend([str(standard_count), str(special_count)])
            total_standard += standard_count
            total_specials += special_count
            grand_standard[store] += standard_count
            grand_specials[store] += special_count
        table.append(row + [str(total_standard), str(total_specials)])

    total_row = ["Total"]
    for store in stores:
        total_row.extend([str(grand_standard[store]), str(grand_specials[store])])
    table.append(total_row + [str(sum(grand_standard.values())), str(sum(grand_specials.values()))])
    return table


def aggregate_oldest_price_age_by_category(
    standard_rows: List[List[str]],
    now: datetime = None,
) -> List[List[str]]:
    """Build a category/store matrix of the oldest Standard Prices age in whole days."""
    now = now or datetime.now()
    standard_data = standard_rows[1:] if standard_rows else []
    stores = sorted({row[0].strip() for row in standard_data if len(row) >= 5 and row[0].strip()})
    ages: dict = defaultdict(dict)

    for row in standard_data:
        if len(row) < 5 or not row[0].strip():
            continue
        store = row[0].strip()
        category = row[8].strip() if len(row) >= 9 and row[8].strip() else "Uncategorised"
        try:
            last_verified = datetime.strptime(row[4].strip(), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        age_days = max(0, (now - last_verified).days)
        ages[category][store] = max(age_days, ages[category].get(store, 0))

    table = [["Category"] + stores + ["Overall Oldest"]]
    for category in sorted(ages.keys()):
        store_ages = [ages[category].get(store) for store in stores]
        overall_age = max(age for age in store_ages if age is not None)
        table.append(
            [category]
            + [str(age) if age is not None else "" for age in store_ages]
            + [str(overall_age)]
        )
    return table


def aggregate_scrape_cost_by_day(rows: List[List[str]]) -> List[List[str]]:
    """Build a Date x Store scrape-cost table for the daily cost chart."""
    data_rows = rows[1:] if rows else []
    stores = sorted({row[2] for row in data_rows if len(row) >= 7 and row[2]})
    by_date: dict = defaultdict(lambda: defaultdict(float))
    cost_recorded: dict = defaultdict(set)

    for row in data_rows:
        if len(row) < 7:
            continue
        timestamp, _source, store, _query, _status, _duration, cost_str = row[:7]
        date = _date_part(timestamp)
        if cost_str:
            by_date[date][store] += _safe_float(cost_str)
            cost_recorded[date].add(store)

    table = [["Date"] + stores + ["Total"]]
    store_totals = defaultdict(float)
    stores_with_cost = set()
    for date in sorted(by_date.keys()):
        date_costs = [by_date[date].get(store, 0.0) for store in stores]
        for store, cost in zip(stores, date_costs):
            if store in cost_recorded[date]:
                store_totals[store] += cost
                stores_with_cost.add(store)
        table.append(
            [date]
            + [
                f"{cost:.4f}" if store in cost_recorded[date] else ""
                for store, cost in zip(stores, date_costs)
            ]
            + [f"{sum(date_costs):.4f}"]
        )
    if by_date:
        table.append(
            ["Total"]
            + [f"{store_totals[store]:.4f}" if store in stores_with_cost else "" for store in stores]
            + [f"{sum(store_totals.values()):.4f}"]
        )
    return table


def aggregate_scrape_issues_by_day(rows: List[List[str]]) -> List[List[str]]:
    """Build a Date x (Total Runs, Issues) table for the scraper health chart."""
    data_rows = rows[1:] if rows else []
    totals: dict = defaultdict(int)
    issues: dict = defaultdict(int)

    for row in data_rows:
        if len(row) < 5:
            continue
        timestamp, _source, _store, _query, status = row[:5]
        date = _date_part(timestamp)
        totals[date] += 1
        if status not in ("ok", "SUCCEEDED"):
            issues[date] += 1

    table = [["Date", "Total Runs", "Issues"]]
    for date in sorted(totals.keys()):
        table.append([date, str(totals[date]), str(issues.get(date, 0))])
    return table


def aggregate_store_health(rows: List[List[str]]) -> List[List[str]]:
    """Build a Store x (Total Runs, Issues, Issue Rate %) table."""
    data_rows = rows[1:] if rows else []
    totals: dict = defaultdict(int)
    issues: dict = defaultdict(int)

    for row in data_rows:
        if len(row) < 5:
            continue
        _timestamp, _source, store, _query, status = row[:5]
        if not store:
            continue
        totals[store] += 1
        if status not in ("ok", "SUCCEEDED"):
            issues[store] += 1

    table = [["Store", "Total Runs", "Issues", "Issue Rate %"]]
    for store in sorted(totals.keys()):
        total = totals[store]
        issue_count = issues.get(store, 0)
        rate = round(100 * issue_count / total, 1) if total else 0.0
        table.append([store, str(total), str(issue_count), f"{rate}"])
    return table


def aggregate_shopping_activity_by_day(rows: List[List[str]]) -> List[List[str]]:
    """Build a Date x (Items Added, Items Ticked, Avg Savings) table."""
    data_rows = rows[1:] if rows else []
    items_added: dict = defaultdict(int)
    items_ticked: dict = defaultdict(int)
    savings_sum: dict = defaultdict(float)
    savings_count: dict = defaultdict(int)

    for row in data_rows:
        if len(row) < 7:
            continue
        timestamp, _user, event_type, _mode, ticked_str, _total, savings_str = row[:7]
        date = _date_part(timestamp)
        if event_type == "item_added":
            items_added[date] += 1
        elif event_type == "shop_completed":
            if ticked_str:
                items_ticked[date] += int(_safe_float(ticked_str))
            if savings_str:
                savings_sum[date] += _safe_float(savings_str)
                savings_count[date] += 1

    dates = sorted(set(items_added) | set(items_ticked) | set(savings_sum))
    table = [["Date", "Items Added", "Items Ticked", "Avg Savings"]]
    for date in dates:
        count = savings_count.get(date, 0)
        avg_savings = round(savings_sum[date] / count, 2) if count else 0.0
        table.append([
            date,
            str(items_added.get(date, 0)),
            str(items_ticked.get(date, 0)),
            f"{avg_savings}",
        ])
    return table


def aggregate_signups_last_n_days(rows: List[List[str]], days: int = 7, today: datetime = None) -> List[List[str]]:
    """Build a Date x New Signups table covering the last N days (net growth)."""
    today = today or datetime.now()
    window_start = (today - timedelta(days=days - 1)).date()
    counts: dict = defaultdict(int)

    for row in rows[1:] if rows else []:
        if not row:
            continue
        created_at = row[-1]
        if not created_at:
            continue
        date_str = _date_part(created_at)
        try:
            date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if date >= window_start:
            counts[date_str] += 1

    table = [["Date", "New Signups"]]
    for offset in range(days):
        date = (window_start + timedelta(days=offset)).isoformat()
        table.append([date, str(counts.get(date, 0))])
    return table


def aggregate_shop_mode_split(rows: List[List[str]]) -> List[List[str]]:
    """Build a Mode x Count table (single vs split) for the shopping-mode pie chart."""
    counts: dict = defaultdict(int)
    for row in rows[1:] if rows else []:
        if len(row) < 4:
            continue
        _timestamp, _user, event_type, mode = row[:4]
        if event_type == "shop_mode_selected" and mode:
            counts[mode] += 1

    table = [["Mode", "Count"]]
    for mode in ("single", "split"):
        table.append([mode.capitalize(), str(counts.get(mode, 0))])
    return table


def _iso_week_label(timestamp: str) -> str:
    date_str = _date_part(timestamp)
    try:
        date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return ""
    year, week, _ = date.isocalendar()
    return f"{year}-W{week:02d}"


def aggregate_engagement_by_week(rows: List[List[str]]) -> List[List[str]]:
    """Build a Week x (Refer Clicks, Contact Clicks) table."""
    refer_counts: dict = defaultdict(int)
    contact_counts: dict = defaultdict(int)

    for row in rows[1:] if rows else []:
        if len(row) < 3:
            continue
        timestamp, _user, event_type = row[:3]
        week = _iso_week_label(timestamp)
        if not week:
            continue
        if event_type == "refer_click":
            refer_counts[week] += 1
        elif event_type == "contact_click":
            contact_counts[week] += 1

    weeks = sorted(set(refer_counts) | set(contact_counts))
    table = [["Week", "Refer Clicks", "Contact Clicks"]]
    for week in weeks:
        table.append([week, str(refer_counts.get(week, 0)), str(contact_counts.get(week, 0))])
    return table
