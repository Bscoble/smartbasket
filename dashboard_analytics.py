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
from typing import List


def _safe_float(value: str, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _date_part(timestamp: str) -> str:
    """Return the YYYY-MM-DD portion of an ISO timestamp or plain date string."""
    return (timestamp or "")[:10]


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
        table.append(row + [str(total_standard), str(total_specials)])
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

    table = [["Date"] + stores]
    for date in sorted(by_date.keys()):
        table.append(
            [date]
            + [
                f"{by_date[date].get(store, 0.0):.4f}" if store in cost_recorded[date] else ""
                for store in stores
            ]
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
