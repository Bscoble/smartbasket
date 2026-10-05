"""Shared catalogue merging for background discovery and refresh."""

from datetime import datetime

from modules.brands import merge_brand_metadata


def merge_products(
    products, store, standard_prices, daily_specials, category_fallback="",
    seen_products=None,
):
    counts = {"new": 0, "refreshed": 0, "duplicates": 0}
    seen = seen_products if seen_products is not None else set()
    for product in products:
        name = product["product_name"].strip()
        key = (store, name.lower())
        identity = (store, product.get("product_id") or product.get("source_url") or key)
        if identity in seen:
            counts["duplicates"] += 1
            continue
        seen.add(identity)
        existing = standard_prices.get(key)
        if existing is None:
            identity_fields = ("product_id", "source_url", "barcode")
            existing_key = next((
                candidate for candidate, entry in standard_prices.items()
                if candidate[0] == store and any(
                    product.get(field) and product.get(field) == entry.get(field)
                    for field in identity_fields
                )
            ), None)
            if existing_key:
                key = existing_key
                existing = standard_prices[key]
        counts["refreshed" if existing is not None else "new"] += 1
        existing = existing or {}
        standard_prices[key] = {
            **existing,
            "price": product["standard_price"],
            "product_name": name,
            "last_verified": datetime.now(),
            "unit_price": product.get("unit_price"),
            "unit_label": product.get("unit_label") or existing.get("unit_label", ""),
            "image_url": product.get("image_url") or existing.get("image_url", ""),
            "category": product.get("category") or existing.get("category") or category_fallback,
            "subcategory": product.get("subcategory") or existing.get("subcategory", ""),
            **merge_brand_metadata(existing, product),
            "barcode": product.get("barcode") or existing.get("barcode", ""),
            "source_url": product.get("source_url") or existing.get("source_url", ""),
            "product_id": product.get("product_id") or existing.get("product_id", ""),
        }
        if product["is_special"] and product["price"] < product["standard_price"]:
            daily_specials[key] = {"price": product["price"], "product_name": name}
        else:
            daily_specials.pop(key, None)
    return counts
