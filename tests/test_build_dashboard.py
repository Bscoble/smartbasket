import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from build_dashboard import write_dashboard


class FakeWorksheet:
    def __init__(self, ws_id):
        self.id = ws_id
        self.cells = {}

    def clear(self):
        self.cells = {}

    def update(self, values, range_name):
        # Parse "A{row}" - only column A writes are used by write_dashboard.
        row = int(range_name[1:])
        for offset, row_values in enumerate(values):
            self.cells[row + offset] = row_values


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
        ws = FakeWorksheet(ws_id=len(self._sheets) + 1)
        self._sheets[title] = ws
        return ws

    def fetch_sheet_metadata(self):
        return {"sheets": [{"properties": {"sheetId": ws.id}, "charts": []} for ws in self._sheets.values()]}

    def batch_update(self, body):
        self.batch_update_calls.append(body)


def test_write_dashboard_places_tables_and_builds_charts():
    spreadsheet = FakeSpreadsheet()
    tables = [
        ("Catalog Size Over Time", [["Date", "Woolworths"], ["2026-08-20", "100"]], "LINE"),
        ("Single vs Split Shopping", [["Mode", "Count"], ["Single", "3"], ["Split", "1"]], "PIE"),
        ("Empty Table", [["Date", "New Signups"]], "COLUMN"),
    ]

    write_dashboard(spreadsheet, tables)

    ws = spreadsheet.worksheet("Performance Dashboard")
    assert ws.cells[1] == ["Catalog Size Over Time"]
    assert ws.cells[2] == ["Date", "Woolworths"]
    assert ws.cells[3] == ["2026-08-20", "100"]

    # Second table starts after a gap: title(1) + header+data(2 rows) + gap(3) = row 7.
    assert ws.cells[7] == ["Single vs Split Shopping"]
    assert ws.cells[8] == ["Mode", "Count"]
    assert ws.cells[9] == ["Single", "3"]
    assert ws.cells[10] == ["Split", "1"]

    # Table with only a header row gets a placeholder instead of a chart.
    assert any(cells == ["Empty Table"] for cells in ws.cells.values())
    assert any(cells == ["No data yet"] for cells in ws.cells.values())

    # Two real charts should have been requested (LINE + PIE), none for the empty table.
    chart_requests = [
        req for call in spreadsheet.batch_update_calls for req in call["requests"] if "addChart" in req
    ]
    assert len(chart_requests) == 2

    line_chart = chart_requests[0]["addChart"]["chart"]["spec"]
    assert line_chart["title"] == "Catalog Size Over Time"
    assert "basicChart" in line_chart
    assert line_chart["basicChart"]["chartType"] == "LINE"

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
