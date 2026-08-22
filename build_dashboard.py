"""
Builds/refreshes the "Performance Dashboard" tab in Google Sheets: writes a
set of small summary tables (computed by dashboard_analytics.py from the raw
log sheets) and adds native Google Sheets charts anchored next to each table.

Every run also re-derives today's catalog size straight from Standard Prices
and logs it to Catalog Size History, so that chart never has a gap purely
because the overnight crawl workflow failed to log it that day.

Run this locally or on a schedule, any time after the log sheets (Catalog
Size History, Scrape Log, User Events, Users) have real data in them:

    python build_dashboard.py
"""

import os
import json
import tomllib

import gspread
from google.oauth2.service_account import Credentials

from config import SPREADSHEET_ID, GOOGLE_SCOPES, WORKSHEET_NAMES, WORKSHEET_CONFIG, STORE_NAMES
from modules.sheets import SheetsManager
from dashboard_analytics import (
    aggregate_category_coverage,
    aggregate_catalog_size_over_time,
    aggregate_oldest_price_age_by_category,
    aggregate_scrape_cost_by_day,
    aggregate_scrape_issues_by_day,
    aggregate_store_health,
    aggregate_shopping_activity_by_day,
    aggregate_signups_last_n_days,
    aggregate_shop_mode_split,
    aggregate_engagement_by_week,
)

CHART_WIDTH_PX = 520
CHART_HEIGHT_PX = 300
CHART_ANCHOR_COL = 9  # column J (0-indexed), to the right of the tables in column A
TABLE_GAP_ROWS = 3


def load_secrets() -> dict:
    """Load secrets from environment variables (CI) or .streamlit/secrets.toml (local)."""
    gcp_json = os.environ.get("GCP_SERVICE_ACCOUNT", "").strip()
    if gcp_json:
        return {"gcp_service_account": json.loads(gcp_json)}

    with open(".streamlit/secrets.toml", "rb") as f:
        return tomllib.load(f)


def build_spreadsheet(secrets: dict) -> gspread.Spreadsheet:
    creds = Credentials.from_service_account_info(secrets["gcp_service_account"], scopes=GOOGLE_SCOPES)
    gc = gspread.authorize(creds)
    return gc.open_by_key(SPREADSHEET_ID)


def _get_or_create_worksheet(spreadsheet: gspread.Spreadsheet, name: str, rows: str, cols: str):
    try:
        return spreadsheet.worksheet(name)
    except gspread.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=name, rows=rows, cols=cols)


def _read_rows(spreadsheet: gspread.Spreadsheet, worksheet_key: str) -> list:
    """Read raw rows from an existing log worksheet; returns [] if it doesn't exist yet."""
    try:
        ws = spreadsheet.worksheet(WORKSHEET_NAMES[worksheet_key])
    except gspread.WorksheetNotFound:
        return []
    return ws.get_all_values()


def _refresh_todays_catalog_size(spreadsheet: gspread.Spreadsheet) -> None:
    """
    Log today's per-store catalog size straight from Standard Prices (the
    only source of truth for current holdings) before charting. Standard
    Prices itself has no history, so it can't replace Catalog Size History
    as the chart's data source - but this keeps that history gap-free even
    on a day the crawl workflow fails to log it.
    """
    sheets_manager = SheetsManager(spreadsheet)
    standard_prices = sheets_manager.load_standard_prices()
    for store in STORE_NAMES:
        store_count = sum(1 for (s, _) in standard_prices if s == store)
        sheets_manager.log_catalog_size(store, store_count)


def build_dashboard_tables(spreadsheet: gspread.Spreadsheet) -> list:
    """
    Read all source log sheets and compute each dashboard table.
    Returns a list of (title, table, chart_type) tuples in display order.
    """
    _refresh_todays_catalog_size(spreadsheet)
    catalog_rows = _read_rows(spreadsheet, "catalog_size_history")
    standard_rows = _read_rows(spreadsheet, "standard_prices")
    daily_special_rows = _read_rows(spreadsheet, "daily_specials")
    scrape_rows = _read_rows(spreadsheet, "scrape_log")
    event_rows = _read_rows(spreadsheet, "user_events")
    user_rows = _read_rows(spreadsheet, "users")

    return [
        ("Catalog Size Over Time", aggregate_catalog_size_over_time(catalog_rows), "LINE"),
        ("Category Coverage - Standard Prices & Daily Specials", aggregate_category_coverage(standard_rows, daily_special_rows), "COLUMN"),
        ("Oldest Standard Price by Category (Days)", aggregate_oldest_price_age_by_category(standard_rows), "COLUMN"),
        ("Daily Scrape Cost by Store (USD)", aggregate_scrape_cost_by_day(scrape_rows), "COLUMN"),
        ("Scraping Issues per Day", aggregate_scrape_issues_by_day(scrape_rows), "COLUMN"),
        ("Store Health - Issue Rate %", aggregate_store_health(scrape_rows), "COLUMN"),
        ("Shopping Activity per Day", aggregate_shopping_activity_by_day(event_rows), "COLUMN"),
        ("New Signups (Last 7 Days)", aggregate_signups_last_n_days(user_rows), "COLUMN"),
        ("Single vs Split Shopping", aggregate_shop_mode_split(event_rows), "PIE"),
        ("Referral & Contact Events by Week", aggregate_engagement_by_week(event_rows), "COLUMN"),
    ]


def _clear_existing_charts(spreadsheet: gspread.Spreadsheet, sheet_id: int) -> None:
    """Delete any charts already on the dashboard sheet before adding fresh ones."""
    metadata = spreadsheet.fetch_sheet_metadata()
    for sheet in metadata.get("sheets", []):
        if sheet.get("properties", {}).get("sheetId") != sheet_id:
            continue
        chart_ids = [chart["chartId"] for chart in sheet.get("charts", [])]
        if chart_ids:
            spreadsheet.batch_update({
                "requests": [{"deleteEmbeddedObject": {"objectId": chart_id}} for chart_id in chart_ids]
            })
        return


def _basic_chart_request(sheet_id: int, chart_type: str, title: str, table: list, start_row: int) -> dict:
    """Build an addChart request for a LINE/COLUMN/BAR chart from a table written at column A."""
    num_rows = len(table) - (1 if table[-1][0] == "Total" else 0)
    num_cols = len(table[0])
    end_row = start_row + num_rows

    full_range = lambda col: {
        "sheetId": sheet_id,
        "startRowIndex": start_row,
        "endRowIndex": end_row,
        "startColumnIndex": col,
        "endColumnIndex": col + 1,
    }

    series = [
        {"series": {"sourceRange": {"sources": [full_range(col)]}}}
        for col in range(1, num_cols)
    ]

    chart_spec = {
        "title": title,
        "basicChart": {
            "chartType": chart_type,
            "headerCount": 1,
            "domains": [{"domain": {"sourceRange": {"sources": [full_range(0)]}}}],
            "series": series,
        },
    }

    return {
        "addChart": {
            "chart": {
                "spec": chart_spec,
                "position": {
                    "overlayPosition": {
                        "anchorCell": {"sheetId": sheet_id, "rowIndex": start_row, "columnIndex": CHART_ANCHOR_COL},
                        "widthPixels": CHART_WIDTH_PX,
                        "heightPixels": CHART_HEIGHT_PX,
                    }
                },
            }
        }
    }


def _pie_chart_request(sheet_id: int, title: str, table: list, start_row: int) -> dict:
    """Build an addChart request for a PIE chart (single domain column, single series column)."""
    end_row = start_row + len(table)
    domain_range = {
        "sheetId": sheet_id,
        "startRowIndex": start_row,
        "endRowIndex": end_row,
        "startColumnIndex": 0,
        "endColumnIndex": 1,
    }
    series_range = {
        "sheetId": sheet_id,
        "startRowIndex": start_row,
        "endRowIndex": end_row,
        "startColumnIndex": 1,
        "endColumnIndex": 2,
    }

    chart_spec = {
        "title": title,
        "pieChart": {
            "legendPosition": "RIGHT_LEGEND",
            "domain": {"sourceRange": {"sources": [domain_range]}},
            "series": {"sourceRange": {"sources": [series_range]}},
        },
    }

    return {
        "addChart": {
            "chart": {
                "spec": chart_spec,
                "position": {
                    "overlayPosition": {
                        "anchorCell": {"sheetId": sheet_id, "rowIndex": start_row, "columnIndex": CHART_ANCHOR_COL},
                        "widthPixels": CHART_WIDTH_PX,
                        "heightPixels": CHART_HEIGHT_PX,
                    }
                },
            }
        }
    }


def _bold_row_request(sheet_id: int, row_index: int, column_count: int) -> dict:
    """Build a native Sheets request that bolds one dashboard row."""
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": row_index,
                "endRowIndex": row_index + 1,
                "startColumnIndex": 0,
                "endColumnIndex": column_count,
            },
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
            "fields": "userEnteredFormat.textFormat.bold",
        }
    }


def write_dashboard(spreadsheet: gspread.Spreadsheet, tables: list) -> None:
    """Write each table to the dashboard sheet in column A and add a chart for each."""
    ws = _get_or_create_worksheet(
        spreadsheet,
        WORKSHEET_NAMES["performance_dashboard"],
        rows=WORKSHEET_CONFIG["performance_dashboard"]["rows"],
        cols=WORKSHEET_CONFIG["performance_dashboard"]["cols"],
    )
    ws.clear()
    _clear_existing_charts(spreadsheet, ws.id)

    chart_requests = []
    format_requests = []
    cursor_row = 0  # 0-indexed row for the API; row (cursor_row + 1) in A1 notation

    for title, table, chart_type in tables:
        ws.update(values=[[title]], range_name=f"A{cursor_row + 1}")
        format_requests.append(_bold_row_request(ws.id, cursor_row, len(table[0])))
        data_start_row = cursor_row + 1
        if len(table) > 1:
            ws.update(values=table, range_name=f"A{data_start_row + 1}")
            format_requests.append(_bold_row_request(ws.id, data_start_row, len(table[0])))
            if table[-1][0] == "Total":
                format_requests.append(_bold_row_request(ws.id, data_start_row + len(table) - 1, len(table[0])))
            if chart_type == "PIE":
                chart_requests.append(_pie_chart_request(ws.id, title, table, data_start_row))
            else:
                chart_requests.append(_basic_chart_request(ws.id, chart_type, title, table, data_start_row))
        else:
            ws.update(values=[["No data yet"]], range_name=f"A{data_start_row + 1}")

        cursor_row = data_start_row + len(table) + TABLE_GAP_ROWS

    if format_requests or chart_requests:
        spreadsheet.batch_update({"requests": format_requests + chart_requests})


def refresh_performance_dashboard(spreadsheet: gspread.Spreadsheet) -> None:
    """Rebuild the dashboard using an already authenticated spreadsheet."""
    tables = build_dashboard_tables(spreadsheet)
    write_dashboard(spreadsheet, tables)
    print(f"Performance Dashboard updated with {len(tables)} tables/charts.")


def build_dashboard() -> None:
    secrets = load_secrets()
    spreadsheet = build_spreadsheet(secrets)
    refresh_performance_dashboard(spreadsheet)


if __name__ == "__main__":
    build_dashboard()
