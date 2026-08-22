import json
import os
import sys
from datetime import datetime, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.product_metadata import (
    parse_allergen_statement,
    parse_woolworths_product_metadata,
    select_metadata_candidates,
)


def test_parse_allergen_statement_keeps_contains_and_may_contain_separate():
    result = parse_allergen_statement("Contains: Gluten, Soy, Wheat May contain: Milk, Peanuts")

    assert result["allergens_contains"] == "Gluten, Soy, Wheat"
    assert result["allergens_may_contain"] == "Milk, Peanuts"


def test_parse_woolworths_metadata_from_labelled_sections():
    html = """
        <section><h3>Ingredients</h3><p>Wheat Flour, Sugar, Soy Lecithin.</p></section>
        <section><h3>Allergens</h3><p><strong>Contains:</strong> Gluten, Soy, Wheat</p>
        <p><strong>May contain:</strong> Milk, Peanuts</p></section>
        <section><h3>Country of origin</h3><p>Made in Indonesia</p></section>
    """

    result = parse_woolworths_product_metadata(html, "https://woolworths.test/product/123")

    assert result["ingredients_raw"] == "Wheat Flour, Sugar, Soy Lecithin."
    assert result["allergens_contains"] == "Gluten, Soy, Wheat"
    assert result["allergens_may_contain"] == "Milk, Peanuts"
    assert result["country_of_origin"] == "Made in Indonesia"
    assert result["source_url"] == "https://woolworths.test/product/123"
    assert result["extraction_status"] == "complete"


def test_parse_woolworths_metadata_prefers_structured_payload():
    payload = {
        "ingredients": "Structured wheat flour",
        "allergenStatement": "Contains: Wheat May contain: Milk",
        "countryOfOrigin": "Australia",
        "nutritionInformation": {"servingSize": "30g", "protein": "3g"},
    }
    html = f'<script type="application/ld+json">{json.dumps(payload)}</script>'

    result = parse_woolworths_product_metadata(html)

    assert result["ingredients_raw"] == "Structured wheat flour"
    assert result["allergens_contains"] == "Wheat"
    assert result["allergens_may_contain"] == "Milk"
    assert json.loads(result["nutrition_json"])["servingSize"] == "30g"


def test_parse_woolworths_metadata_from_next_data_fields():
    payload = {
        "props": {"pageProps": {"pdDetails": {
            "AdditionalAttributes": {
                "ingredients": "Milk",
                "allergencontains": "Milk",
                "allergenmaycontain": "Soy",
                "nutritionalinformation": json.dumps({"Attributes": [{"Name": "Protein", "Value": "3.3g"}]}),
            },
            "CountryOfOriginLabel": {"CountryOfOrigin": "Australia"},
        }}}
    }
    html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'

    result = parse_woolworths_product_metadata(html)

    assert result["ingredients_raw"] == "Milk"
    assert result["allergens_contains"] == "Milk"
    assert result["allergens_may_contain"] == "Soy"
    assert result["country_of_origin"] == "Australia"
    assert json.loads(result["nutrition_json"])["Attributes"][0]["Name"] == "Protein"
    assert result["extraction_status"] == "complete"


def test_select_metadata_candidates_is_woolworths_only_deduplicated_and_stale_aware():
    now = datetime(2026, 8, 22, 12, 0, 0)
    standard_prices = {
        ("Woolworths", "tim tam"): {
            "barcode": "9310072026817",
            "product_name": "Arnott's Tim Tam",
            "source_url": "https://woolworths.test/tim-tam",
        },
        ("Woolworths", "tim tam duplicate"): {
            "barcode": "9310072026817",
            "product_name": "Arnott's Tim Tam",
            "source_url": "https://woolworths.test/tim-tam",
        },
        ("Woolworths", "fresh milk"): {
            "barcode": "9300000000018",
            "source_url": "https://woolworths.test/milk",
        },
        ("Coles", "bread"): {"source_url": "https://coles.test/bread"},
    }
    existing = {
        "9300000000018": {
            "last_verified": now - timedelta(days=10),
            "extraction_status": "complete",
        },
    }

    candidates = select_metadata_candidates(
        standard_prices,
        existing,
        limit=10,
        max_age_days=180,
        failed_retry_days=14,
        now=now,
    )

    assert candidates == [{
        "barcode": "9310072026817",
        "canonical_name": "Arnott's Tim Tam",
        "brand": "",
        "source_retailer": "Woolworths",
        "source_url": "https://woolworths.test/tim-tam",
    }]