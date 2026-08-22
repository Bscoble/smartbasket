import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.dietary import has_gluten_free_claim, product_is_gluten_free
from modules.catalog_matching import find_local_price_matches
from modules.sheets import SheetsManager


def test_gluten_free_requires_an_explicit_positive_phrase():
    assert has_gluten_free_claim("Gluten Free White Bread")
    assert has_gluten_free_claim("Certified gluten-free")
    assert not has_gluten_free_claim("Contains gluten, wheat and soy")
    assert not has_gluten_free_claim("No allergen statement available")


def test_gluten_free_can_be_confirmed_from_joined_allergen_metadata():
    entry = {
        "product_name": "Seeded Bread 500g",
        "barcode": "9300000000018",
        "source_url": "https://retailer.test/seeded-bread",
    }
    metadata = {
        "9300000000018": {
            "allergens_raw": "Gluten Free",
            "allergens_contains": "",
            "allergens_may_contain": "Milk",
        }
    }

    assert product_is_gluten_free("seeded bread 500g", entry, metadata)


class FakeWorksheet:
    def __init__(self, values):
        self.values = [row[:] for row in values]
        self.row_count = max(2, len(values))

    def get_all_values(self):
        return [row[:] for row in self.values]


class FakeSpreadsheet:
    def __init__(self, standard_prices, metadata):
        self.sheets = {
            "Standard Prices": FakeWorksheet(standard_prices),
            "Product Metadata": FakeWorksheet(metadata),
        }

    def worksheet(self, name):
        return self.sheets[name]


def test_scraped_product_search_only_surfaces_positive_gluten_free_claims():
    standard_prices = [
        ["Store", "Item", "Price", "Product Name", "Last Verified", "Unit Price", "Unit Label", "Image URL", "Category", "Subcategory", "Brand", "Brand Source", "Brand Confidence", "Barcode", "Source URL"],
        ["Coles", "white bread 500g", "4.50", "White Bread 500g", "2026-08-22 12:00:00", "", "", "", "Bakery", "Bread", "", "", "", "9300000000018", "https://coles.test/bread"],
        ["Woolworths", "gluten free white bread 500g", "6.50", "Gluten Free White Bread 500g", "2026-08-22 12:00:00", "", "", "", "Bakery", "Bread", "", "", "", "", ""],
    ]
    metadata = [
        ["Metadata Key", "Barcode", "Canonical Name", "Brand", "Ingredients Raw", "Allergens Raw", "Allergens Contains", "Allergens May Contain", "Nutrition JSON", "Country of Origin", "Source Retailer", "Source URL", "Last Verified", "Extraction Status"],
        ["9300000000018", "9300000000018", "White Bread 500g", "", "Rice flour", "Contains: Gluten", "Gluten", "", "", "Australia", "Coles", "https://coles.test/bread", "2026-08-22 12:00:00", "complete"],
    ]
    manager = SheetsManager(FakeSpreadsheet(standard_prices, metadata))

    results = manager.search_scraped_products("white bread 500g", gluten_free_only=True)

    assert [result["title"] for result in results] == ["gluten free white bread 500g"]


def test_local_price_matching_applies_dietary_eligibility():
    prices = {
        ("Coles", "white bread 500g"): {
            "product_name": "White Bread 500g",
            "price": 4.50,
        },
        ("Woolworths", "gluten free white bread 500g"): {
            "product_name": "Gluten Free White Bread 500g",
            "price": 6.50,
        },
    }

    matches = find_local_price_matches(
        "white bread 500g",
        ["Coles", "Woolworths"],
        prices,
        is_valid=lambda _entry: True,
        is_eligible=lambda item, entry: product_is_gluten_free(item, entry, {}),
    )

    assert set(matches) == {"Woolworths"}


class PreferenceWorksheet:
    def __init__(self):
        self.values = [
            ["Woolworths", "Coles", "Aldi", "IGA"],
            ["True", "True", "True", "False"],
        ]
        self.row_count = 2
        self.col_count = 4

    def get_all_values(self):
        return [row[:] for row in self.values]

    def update(self, range_name, values):
        assert range_name == "E1:E2"
        while len(self.values[0]) < 5:
            self.values[0].append("")
            self.values[1].append("")
        self.values[0][4] = values[0][0]
        self.values[1][4] = values[1][0]


def test_gluten_free_preference_persists_without_changing_store_columns():
    worksheet = PreferenceWorksheet()

    class PreferenceSpreadsheet:
        def worksheet(self, name):
            assert name == "Preferences"
            return worksheet

    manager = SheetsManager(PreferenceSpreadsheet())

    assert manager.load_dietary_preferences() == {"Gluten Free": False}
    assert manager.save_dietary_preferences({"Gluten Free": True})
    assert manager.load_dietary_preferences() == {"Gluten Free": True}
    assert worksheet.values[1][:4] == ["True", "True", "True", "False"]