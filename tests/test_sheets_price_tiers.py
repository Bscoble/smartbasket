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
    assert prices[("Aldi", "milk")]["barcode"] == ""


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
            "barcode": "4000417025005",
            "source_url": "https://aldi.test/choceur",
        }
    })

    assert spreadsheet.worksheet_value.values[0][-5:] == ["Brand", "Brand Source", "Brand Confidence", "Barcode", "Source URL"]
    assert spreadsheet.worksheet_value.values[1][-5:] == ["Choceur", "retailer", "high", "4000417025005", "https://aldi.test/choceur"]


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
        barcode="9300601433247",
        source_url="https://coles.test/milk",
    )

    assert spreadsheet.worksheet_value.values[0][-5:] == ["Brand", "Brand Source", "Brand Confidence", "Barcode", "Source URL"]
    assert spreadsheet.worksheet_value.values[1][-5:] == ["Coles", "retailer", "high", "9300601433247", "https://coles.test/milk"]


def test_upsert_and_load_product_metadata_keeps_explicit_allergen_claims():
    spreadsheet = FakeSpreadsheet([])
    manager = SheetsManager(spreadsheet)
    entry = {
        "barcode": "9310072026817",
        "canonical_name": "Arnott's Tim Tam Original 200g",
        "brand": "Arnott's",
        "ingredients_raw": "Wheat flour, sugar",
        "allergens_raw": "Contains: Wheat May contain: Milk",
        "allergens_contains": "Wheat",
        "allergens_may_contain": "Milk",
        "nutrition_json": '{"servingSize":"20g"}',
        "country_of_origin": "Australia",
        "source_retailer": "Woolworths",
        "source_url": "https://woolworths.test/tim-tam",
        "last_verified": datetime(2026, 8, 22, 12, 0, 0),
        "extraction_status": "complete",
    }

    assert manager.upsert_product_metadata(entry)
    metadata = manager.load_product_metadata()["9310072026817"]

    assert metadata["allergens_contains"] == "Wheat"
    assert metadata["allergens_may_contain"] == "Milk"
    assert metadata["source_retailer"] == "Woolworths"


def test_find_product_by_barcode_returns_local_product_and_store_coverage():
    spreadsheet = FakeSpreadsheet([
        ["Store", "Item", "Price", "Product Name", "Last Verified", "Unit Price", "Unit Label", "Image URL", "Category", "Subcategory", "Brand", "Brand Source", "Brand Confidence", "Barcode"],
        ["Coles", "tim tam original 200g", "4.50", "Arnott's Tim Tam Original 200g", "2026-08-21 12:00:00", "", "", "", "Snacks", "Biscuits", "Arnott's", "retailer", "high", "9310072026817"],
        ["Woolworths", "tim tam original 200g", "4.80", "Arnott's Tim Tam Original 200g", "2026-08-21 12:00:00", "", "", "https://scraped.test/tim-tam.png", "Snacks", "Biscuits", "Arnott's", "retailer", "high", "9310072026817"],
    ])
    manager = SheetsManager(spreadsheet)

    result = manager.find_product_by_barcode("9310 0720 2681 7")

    assert result["title"] == "Arnott's Tim Tam Original 200g"
    assert result["image_url"] == "https://scraped.test/tim-tam.png"
    assert result["stores"] == ["Coles", "Woolworths"]
    assert manager.find_product_by_barcode("9999999999999") is None


def test_tissue_search_excludes_toilet_paper_and_paper_towels():
    spreadsheet = FakeSpreadsheet([
        ["Store", "Item", "Price", "Product Name", "Last Verified", "Unit Price", "Unit Label", "Image URL", "Category", "Subcategory"],
        ["Coles", "facial tissues 95 pack", "3.00", "Facial Tissues 95 Pack", "2026-08-21 12:00:00", "", "", "", "Cleaning & Household", "Toilet Paper, Tissues & Paper Towels"],
        ["Coles", "toilet paper tissues 12 pack", "7.00", "Toilet Paper Tissues 12 Pack", "2026-08-21 12:00:00", "", "", "", "Cleaning & Household", "Toilet Paper, Tissues & Paper Towels"],
        ["Aldi", "toilet tissue 18 pack", "6.00", "Toilet Tissue 18 Pack", "2026-08-21 12:00:00", "", "", "", "Cleaning & Household", "Toilet Paper, Tissues & Paper Towels"],
        ["Aldi", "paper towels tissues 2 pack", "4.00", "Paper Towels Tissues 2 Pack", "2026-08-21 12:00:00", "", "", "", "Cleaning & Household", "Toilet Paper, Tissues & Paper Towels"],
        ["Aldi", "flushable wipes 40 pack", "2.49", "Flushable Wipes 40 Pack", "2026-08-21 12:00:00", "", "", "", "Cleaning & Household", "Toilet Paper, Tissues & Paper Towels"],
    ])
    manager = SheetsManager(spreadsheet)

    results = manager.search_scraped_products("tissues", limit=None)

    assert [result["title"] for result in results] == ["facial tissues 95 pack"]
