import os
import sys
from datetime import datetime, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.sheets import SheetsManager
from config import STANDARD_PRICE_MAX_AGE_DAYS


class FakeWorksheet:
    def __init__(self, values):
        self.values = [row[:] for row in values]
        self.row_count = max(len(self.values), 2)

    def get_all_values(self):
        return [row[:] for row in self.values]

    def append_row(self, row):
        self.values.append(list(row))

    def append_rows(self, rows):
        self.values.extend(list(row) for row in rows)

    def clear(self):
        self.values = []

    def update(self, range_name, values):
        row_number = int(range_name.split(":", 1)[0][1:])
        self.values[row_number - 1] = list(values[0])


class FakeSpreadsheet:
    def __init__(self, values):
        self.worksheet_value = FakeWorksheet(values)

    def worksheet(self, _name):
        return self.worksheet_value


def test_standard_price_valid_within_max_age():
    manager = SheetsManager(spreadsheet=None)
    entry = {"price": 4.5, "last_verified": datetime.now() - timedelta(days=1)}
    assert manager.is_standard_price_valid(entry) is True


def test_standard_price_invalid_once_stale():
    manager = SheetsManager(spreadsheet=None)
    entry = {
        "price": 4.5,
        "last_verified": datetime.now() - timedelta(days=STANDARD_PRICE_MAX_AGE_DAYS + 1),
    }
    assert manager.is_standard_price_valid(entry) is False


def test_standard_price_invalid_when_missing_timestamp():
    manager = SheetsManager(spreadsheet=None)
    assert manager.is_standard_price_valid({"price": 4.5}) is False


def test_load_standard_prices_keeps_brand_metadata_and_supports_legacy_rows():
    spreadsheet = FakeSpreadsheet([
        ["Store", "Item", "Price", "Product Name", "Last Verified", "Unit Price", "Unit Label", "Image URL", "Category", "Subcategory", "Brand", "Brand Source", "Brand Confidence"],
        ["Coles", "Tim Tam", "4.50", "Arnott's Tim Tam", "2026-08-21 12:00:00", "", "", "", "Snacks", "Biscuits", "Arnott's", "retailer", "high"],
        ["Aldi", "Milk", "3.20", "Full Cream Milk", "2026-08-21 12:00:00", "", "", "", "Dairy", "Milk"],
    ])
    manager = SheetsManager(spreadsheet)

    prices = manager.load_standard_prices()

    assert prices[("Coles", "tim tam")]["brand"] == "Arnott's"
    assert prices[("Coles", "tim tam")]["brand_source"] == "retailer"
    assert prices[("Coles", "tim tam")]["brand_confidence"] == "high"
    assert prices[("Aldi", "milk")]["brand"] == ""
    assert prices[("Aldi", "milk")]["brand_source"] == ""
    assert prices[("Aldi", "milk")]["brand_confidence"] == ""


def test_save_standard_prices_writes_brand_metadata_columns():
    spreadsheet = FakeSpreadsheet([])
    manager = SheetsManager(spreadsheet)

    assert manager.save_standard_prices({
        ("Aldi", "choceur chocolate"): {
            "price": 3.99,
            "product_name": "Choceur Milk Chocolate",
            "last_verified": datetime(2026, 8, 22, 12, 0, 0),
            "brand": "Choceur",
            "brand_source": "retailer",
            "brand_confidence": "high",
        }
    })

    assert spreadsheet.worksheet_value.values[0][-3:] == ["Brand", "Brand Source", "Brand Confidence"]
    assert spreadsheet.worksheet_value.values[1][-3:] == ["Choceur", "retailer", "high"]


def test_upsert_standard_price_migrates_legacy_header():
    spreadsheet = FakeSpreadsheet([
        ["Store", "Item", "Price", "Product Name", "Last Verified", "Unit Price", "Unit Label", "Image URL", "Category", "Subcategory"],
        ["Coles", "Milk", "3.00", "Coles Milk", "2026-08-21 12:00:00", "", "", "", "Dairy", "Milk"],
    ])
    manager = SheetsManager(spreadsheet)

    assert manager.upsert_standard_price(
        "Coles",
        "Milk",
        3.20,
        product_name="Coles Milk",
        brand="Coles",
        brand_source="retailer",
        brand_confidence="high",
    )

    assert spreadsheet.worksheet_value.values[0][-3:] == ["Brand", "Brand Source", "Brand Confidence"]
    assert spreadsheet.worksheet_value.values[1][-3:] == ["Coles", "retailer", "high"]
