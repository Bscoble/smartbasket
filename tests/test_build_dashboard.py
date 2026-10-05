import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from build_dashboard import _read_rows, build_dashboard_tables, write_dashboard


class FakeWorksheet:
    def __init__(self, ws_id, rows, cols):
        self.id = ws_id
        self.row_count = int(rows)
        self.col_count = int(cols)
        self.cells = {}

    def resize(self, rows, cols):
        self.row_count = rows
        self.col_count = cols

    def clear(self):
        self.cells = {}

    def update(self, values, range_name):
        # Parse "A{row}" - only column A writes are used by write_dashboard.
        row = int(range_name[1:])
        assert row + len(values) - 1 <= self.row_count
        assert max(len(row_values) for row_values in values) <= self.col_count
        for offset, row_values in enumerate(values):
            self.cells[row + offset] = row_values

    def get_all_values(self):
        return []


class FakeSpreadsheet:
    def __init__(self):
        self._sheets = {}
        self.batch_update_calls = []

    def worksheet(self, name):
        if name not in self._sheets:
            import gspread
            raise gspread.WorksheetNotFound(name)
        return self._sheets[name]

    def add_worksheet(self, title, rows, cols):
        ws = FakeWorksheet(ws_id=len(self._sheets) + 1, rows=rows, cols=cols)
        self._sheets[title] = ws
        return ws

    def fetch_sheet_metadata(self):
        return {"sheets": [{"properties": {"sheetId": ws.id}, "charts": []} for ws in self._sheets.values()]}

    def batch_update(self, body):
        self.batch_update_calls.append(body)


def test_write_dashboard_places_tables_and_builds_charts():
    spreadsheet = FakeSpreadsheet()
    tables = [
        (
            "Catalog Size Over Time",
            [["Date", "Woolworths"], ["2026-08-20", "100"], ["Total", "100"]],
            "LINE",
        ),
        ("Single vs Split Shopping", [["Mode", "Count"], ["Single", "3"], ["Split", "1"]], "PIE"),
        ("Empty Table", [["Date", "New Signups"]], "COLUMN"),
    ]

    write_dashboard(spreadsheet, tables)

    ws = spreadsheet.worksheet("Performance Dashboard")
    assert ws.cells[1] == ["Catalog Size Over Time"]
    assert ws.cells[2] == ["Date", "Woolworths"]
    assert ws.cells[3] == ["2026-08-20", "100"]
    assert ws.cells[4] == ["Total", "100"]

    # Second table starts after a gap: title(1) + table(3 rows) + gap(3) = row 8.
    assert ws.cells[8] == ["Single vs Split Shopping"]
    assert ws.cells[9] == ["Mode", "Count"]
    assert ws.cells[10] == ["Single", "3"]
    assert ws.cells[11] == ["Split", "1"]

    # Table with only a header row gets a placeholder instead of a chart.
    assert any(cells == ["Empty Table"] for cells in ws.cells.values())
    assert any(cells == ["No data yet"] for cells in ws.cells.values())

    # Two real charts should have been requested (LINE + PIE), none for the empty table.
    chart_requests = [
        req for call in spreadsheet.batch_update_calls for req in call["requests"] if "addChart" in req
    ]
    assert len(chart_requests) == 2

    bold_requests = [
        req for call in spreadsheet.batch_update_calls for req in call["requests"] if "repeatCell" in req
    ]
    assert len(bold_requests) == 6
    assert all(
        req["repeatCell"]["cell"]["userEnteredFormat"]["textFormat"]["bold"]
        for req in bold_requests
    )

    line_chart = chart_requests[0]["addChart"]["chart"]["spec"]
    assert line_chart["title"] == "Catalog Size Over Time"
    assert "basicChart" in line_chart
    assert line_chart["basicChart"]["chartType"] == "LINE"
    line_domain = line_chart["basicChart"]["domains"][0]["domain"]["sourceRange"]["sources"][0]
    assert line_domain["endRowIndex"] == 3

    pie_chart = chart_requests[1]["addChart"]["chart"]["spec"]
    assert pie_chart["title"] == "Single vs Split Shopping"
    assert "pieChart" in pie_chart


def test_write_dashboard_clears_existing_charts_before_adding_new_ones():
    spreadsheet = FakeSpreadsheet()
    ws = spreadsheet.add_worksheet("Performance Dashboard", rows="200", cols="20")

    def fetch_metadata_with_chart():
        return {"sheets": [{"properties": {"sheetId": ws.id}, "charts": [{"chartId": 999}]}]}

    spreadsheet.fetch_sheet_metadata = fetch_metadata_with_chart

    write_dashboard(spreadsheet, [("Table", [["A", "B"], ["1", "2"]], "COLUMN")])

    delete_calls = [
        req for call in spreadsheet.batch_update_calls for req in call["requests"] if "deleteEmbeddedObject" in req
    ]
    assert delete_calls == [{"deleteEmbeddedObject": {"objectId": 999}}]


def test_write_dashboard_grows_existing_grid_to_fit_tables():
    spreadsheet = FakeSpreadsheet()
    ws = spreadsheet.add_worksheet("Performance Dashboard", rows="232", cols="20")
    table = [["Day", "Count"]] + [[str(day), str(day)] for day in range(240)]

    write_dashboard(spreadsheet, [("History", table, "LINE")])

    assert ws.row_count >= 242
    assert ws.col_count == 20
    assert ws.cells[242] == ["239", "239"]


def test_write_dashboard_does_not_shrink_existing_grid():
    spreadsheet = FakeSpreadsheet()
    ws = spreadsheet.add_worksheet("Performance Dashboard", rows="500", cols="20")

    write_dashboard(spreadsheet, [("History", [["Day", "Count"], ["1", "2"]], "LINE")])

    assert ws.row_count == 500
    assert ws.col_count == 20


def test_read_rows_returns_empty_for_missing_or_unconfigured_worksheet():
    spreadsheet = FakeSpreadsheet()

    assert _read_rows(spreadsheet, "users") == []
    assert _read_rows(spreadsheet, "catalogue_metrics") == []


def test_build_dashboard_tables_includes_new_metrics_without_chart_types(monkeypatch):
    import build_dashboard as dashboard

    rows = {
        "catalog_size_history": [["Date", "Store", "Product Count"]],
        "standard_prices": [
            ["Store", "Item", "Price", "Product Name", "Last Verified"],
            ["Coles", "Milk", "4.00", "Milk", "2026-10-05 09:00:00"],
        ],
        "daily_specials": [["Store", "Item", "Price", "Product Name", "Date"]],
        "scrape_log": [
            ["Timestamp", "Source", "Store", "Query", "Status", "Duration Secs", "Cost USD"],
            ["2026-10-05T09:00:00", "crawl", "Coles", "milk", "ok", "1", "0.10"],
        ],
        "user_events": [
            ["Timestamp", "User ID", "Event Type", "Mode", "Items Ticked", "Items Total", "Savings"],
            ["2026-10-05T09:00:00", "private@example.com", "comparison_run", "", "2", "3", ""],
        ],
        "users": [["Email", "Created At"]],
        "catalogue_metrics": [
            ["Timestamp", "Source", "Store", "Query", "New Products", "Refreshed Products", "Duplicate Products"],
            ["2026-10-05T09:00:00", "crawl", "Coles", "milk", "1", "2", "0"],
        ],
        "scraper_budget": [
            ["Timestamp", "Reservation ID", "Source", "Store", "Reserved USD", "Actual USD", "Status"],
            ["2026-10-05T09:00:00", "r1", "crawl", "Coles", "1.00", "", "reserved"],
        ],
    }
    monkeypatch.setattr(dashboard, "_refresh_todays_catalog_size", lambda _spreadsheet: None)
    monkeypatch.setattr(dashboard, "_read_rows", lambda _spreadsheet, key: rows[key])

    tables = build_dashboard_tables(FakeSpreadsheet())
    by_title = {title: (table, chart_type) for title, table, chart_type in tables}

    assert by_title["Catalogue Discovery Metrics"] == (
        [
            ["Date", "Source", "Store", "New Products", "Refreshed Products", "Duplicate Products"],
            ["2026-10-05", "crawl", "Coles", "1", "2", "0"],
        ],
        None,
    )
    assert by_title["Comparison Basket Coverage"][0][1][3:] == ["2", "3", "66.7"]
    assert by_title["Catalogue Freshness by Store"][0][1][1:4] == ["1", "0", "0"]
    assert all(chart_type is None for title, _table, chart_type in tables if title in {
        "Catalogue Discovery Metrics",
        "Scrape Requests & Errors",
        "Recorded Cost per New or Refreshed Product",
        "Scraper Reservations and Actuals",
        "Catalogue Freshness by Store",
        "Comparison Basket Coverage",
    })


def test_write_dashboard_skips_chart_for_table_only_sections():
    spreadsheet = FakeSpreadsheet()

    write_dashboard(spreadsheet, [("Cost and Counts", [["Cost", "Requests"], ["", "2"]], None)])

    assert spreadsheet.worksheet("Performance Dashboard").cells[2] == ["Cost", "Requests"]
    chart_requests = [
        req for call in spreadsheet.batch_update_calls for req in call["requests"] if "addChart" in req
    ]
    assert chart_requests == []
