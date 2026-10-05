"""Manual, budgeted Coles actor trial; never changes the production provider."""

import os
from urllib.parse import quote

from discover_requested_products import build_dependencies
from modules.pricing import PriceScraper
from helpers import is_valid_price


def evaluate_pages(pages):
    if len(pages) != 2:
        raise ValueError("A pagination trial requires exactly two result pages")
    identities = []
    for page in pages:
        products = [PriceScraper._extract_bulk_product_info("Coles", product) for product in page]
        if not products or any(
            not product or not product["product_id"] or not product["source_url"]
            or not is_valid_price(product["price"])
            or not is_valid_price(product["standard_price"]) or not product["unit_label"]
            for product in products
        ):
            return {"passed": False, "reason": "Missing products, identity, URL, price or unit metadata"}
        identities.append({product["product_id"] for product in products})
    overlap = len(identities[0] & identities[1])
    new_fraction = len(identities[1] - identities[0]) / len(identities[1])
    return {
        "passed": new_fraction >= 0.8,
        "page_one_unique": len(identities[0]),
        "page_two_unique": len(identities[1]),
        "overlap": overlap,
        "page_two_new_fraction": new_fraction,
        "reason": "Pagination accepted" if new_fraction >= 0.8 else "Pages repeat too many products",
    }


def main():
    actor = os.environ.get("COLES_CANDIDATE_ACTOR", "").strip()
    if not actor:
        raise RuntimeError("Set COLES_CANDIDATE_ACTOR to an explicitly chosen trial actor")
    manager, scraper, budget = build_dependencies()
    budget.source = "coles_provider_trial"
    scraper.usage_logger = lambda **kwargs: manager.log_scrape_run(
        source="coles_provider_trial", **kwargs,
    )
    from apify_client import ApifyClient
    client = ApifyClient(scraper.apify_token)
    pages = []
    for page in (1, 2):
        url = f"https://www.coles.com.au/search/products?q={quote('biscuits')}&page={page}"
        run = scraper._call_actor(client, actor, "Coles", {
            "urls": [url], "max_items_per_url": 20,
        })
        if run is None or run.status != "SUCCEEDED":
            raise RuntimeError("Candidate actor did not succeed; production unchanged")
        products = scraper._iter_apify_products(client.dataset(run.default_dataset_id).list_items().items)
        scraper._log_apify_usage("Coles", url, run, len(products))
        pages.append(products)
    result = evaluate_pages(pages)
    print(f"Coles provider trial: {result}")
    if not result["passed"]:
        raise RuntimeError("Candidate failed the compatibility/pagination trial")
    print("Trial passed. Production provider remains unchanged pending review of results and billing.")


if __name__ == "__main__":
    main()
