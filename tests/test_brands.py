import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.brands import merge_brand_metadata, resolve_brand


def test_explicit_retailer_brand_has_high_confidence():
    assert resolve_brand("Milk 2L", "Dairy Farmers", "Coles") == {
        "brand": "Dairy Farmers",
        "brand_source": "retailer",
        "brand_confidence": "high",
    }


def test_store_private_label_uses_controlled_mapping():
    assert resolve_brand("Choceur Milk Chocolate 200g", store="Aldi") == {
        "brand": "Choceur",
        "brand_source": "private_label_mapping",
        "brand_confidence": "high",
    }


def test_known_brand_name_inference_is_medium_confidence():
    assert resolve_brand("Arnott's Tim Tam Original 200g", store="Coles") == {
        "brand": "Arnott's",
        "brand_source": "name_inference",
        "brand_confidence": "medium",
    }


def test_descriptive_first_word_is_not_inferred_as_brand():
    assert resolve_brand("Fresh Full Cream Milk 2L", store="Coles") == {
        "brand": "",
        "brand_source": "",
        "brand_confidence": "",
    }


def test_explicit_retailer_brand_replaces_weaker_existing_inference():
    existing = {
        "brand": "Coles",
        "brand_source": "private_label_mapping",
        "brand_confidence": "high",
    }
    assert resolve_brand("Dairy Farmers Milk", "Dairy Farmers", "Coles", existing) == {
        "brand": "Dairy Farmers",
        "brand_source": "retailer",
        "brand_confidence": "high",
    }


def test_manual_brand_is_never_overwritten():
    existing = {
        "brand": "Corrected Brand",
        "brand_source": "manual",
        "brand_confidence": "high",
    }
    assert resolve_brand("Dairy Farmers Milk", "Dairy Farmers", "Coles", existing) == existing


def test_merge_keeps_retailer_metadata_over_name_inference():
    retailer = {"brand": "Arnott's", "brand_source": "retailer", "brand_confidence": "high"}
    inferred = {"brand": "Arnott's", "brand_source": "name_inference", "brand_confidence": "medium"}

    assert merge_brand_metadata(retailer, inferred) == retailer