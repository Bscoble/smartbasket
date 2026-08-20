import tomllib
from datetime import timedelta
from urllib.parse import quote
import time
from apify_client import ApifyClient

# Read APIFY_TOKEN using tomllib
with open(".streamlit/secrets.toml", "rb") as f:
    secrets = tomllib.load(f)
token = secrets["APIFY_TOKEN"]

client = ApifyClient(token)

stores = {
    "Woolworths": {
        "api_actor": "stealth_mode/woolworths-product-search-scraper",
        "search_url": "https://www.woolworths.com.au/shop/search/products?searchTerm={}",
    },
    "Coles": {
        "api_actor": "stealth_mode/coles-product-search-scraper",
        "search_url": "https://www.coles.com.au/search/products?q={}",
    },
}

APIFY_DEFAULT_CONFIG = {
    "ignore_url_failures": True,
    "max_items_per_url": 1,
    "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
}

items = ["A2 Milk full cream 2L", "Arnott's Bbq shapes", "arnotts tim tams"]

for store_name, store_cfg in stores.items():
    for item in items:
        print(f"=== Testing Store: {store_name} | Item: {item} ===")
        search_url = store_cfg["search_url"].format(quote(item))
        run_input = {
            "urls": [search_url],
            **APIFY_DEFAULT_CONFIG
        }
        actor = store_cfg["api_actor"]
        
        start_time = time.time()
        try:
            run = client.actor(actor).call(
                run_input=run_input,
                wait_duration=timedelta(seconds=60)
            )
            elapsed = time.time() - start_time
            if run is None:
                print(f"Store: {store_name}")
                print(f"Item: {item}")
                print("Run status: None")
                print(f"Elapsed seconds: {elapsed:.2f}")
                continue
            
            status = run.get("status") if isinstance(run, dict) else getattr(run, "status", None)
            dataset_id = run.get("defaultDatasetId") if isinstance(run, dict) else getattr(run, "default_dataset_id", None)
            
            print(f"Store: {store_name}")
            print(f"Item: {item}")
            print(f"Run status: {status}")
            print(f"Elapsed seconds: {elapsed:.2f}")
            
            if dataset_id:
                dataset_items = client.dataset(dataset_id).list_items().items
                print(f"Number of dataset items: {len(dataset_items)}")
                if len(dataset_items) > 0:
                    first_item = dataset_items[0]
                    print(f"Top-level keys: {list(first_item.keys())}")
                    products = first_item.get("products", [])
                    if products is None:
                        products = []
                    print(f"Number of nested products: {len(products)}")
                    if len(products) > 0:
                        p = products[0]
                        price_fields = {k: p.get(k) for k in ["price", "pricing", "instore_price"] if k in p}
                        print(f"First product name: {p.get('name')}")
                        print(f"First product price fields: {price_fields}")
                else:
                    print("Number of nested products: 0")
            else:
                print("No dataset ID found")
                
        except Exception as e:
            elapsed = time.time() - start_time
            print(f"Store: {store_name}")
            print(f"Item: {item}")
            print(f"Elapsed seconds: {elapsed:.2f}")
            print(f"Exception type: {type(e).__name__}")
            print(f"Exception message: {str(e)}")
        print()
