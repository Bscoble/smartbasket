import os
import json
from datetime import timedelta
from apify_client import ApifyClient

# 1. Read APIFY_TOKEN
token = None
secrets_path = ".streamlit/secrets.toml"
if os.path.exists(secrets_path):
    with open(secrets_path, "r") as f:
        for line in f:
            if "APIFY_TOKEN" in line:
                parts = line.split("=", 1)
                if len(parts) == 2:
                    token = parts[1].strip().strip('"').strip("'")
                    break

if not token:
    print("Could not find APIFY_TOKEN in .streamlit/secrets.toml")
    exit(1)

client = ApifyClient(token)

# 2. Woolworths Scraper run
woolworths_input = {
    'urls': ['https://www.woolworths.com.au/shop/search/products?searchTerm=milk'],
    'ignore_url_failures': True,
    'max_items_per_url': 1,
    'proxy': {
        'useApifyProxy': True,
        'apifyProxyGroups': ['RESIDENTIAL']
    }
}

print("Running Woolworths scraper...")
try:
    woolworths_run = client.actor('stealth_mode/woolworths-product-search-scraper').call(
        run_input=woolworths_input,
        wait_duration=timedelta(seconds=40)
    )
    wool_dataset_id = woolworths_run.get("defaultDatasetId") if isinstance(woolworths_run, dict) else getattr(woolworths_run, "default_dataset_id", None)
    
    if wool_dataset_id:
        print(f"Woolworths Dataset ID: {wool_dataset_id}")
        wool_items = client.dataset(wool_dataset_id).list_items().items
        print(f"Woolworths returned {len(wool_items)} dataset items.")
        if wool_items:
            # We want to print:
            # - FULL first dataset item as pretty JSON (all keys at the top level)
            # - For 'products' list if present, print just the first product's full JSON.
            first_item = wool_items[0]
            print("=== Woolworths First Dataset Item Type & Top-level Keys ===")
            print(f"Keys: {list(first_item.keys())}")
            
            # Create a copy to print top-level structure (but limiting the products list to only the first product if present)
            output_item = {}
            for k, v in first_item.items():
                if k == 'products' and isinstance(v, list):
                    if len(v) > 0:
                        output_item[k] = [v[0]]  # just the first product's full JSON
                    else:
                        output_item[k] = []
                else:
                    output_item[k] = v
            print("=== Woolworths First Dataset Item Pretty JSON ===")
            print(json.dumps(output_item, indent=2))
        else:
            print("Woolworths dataset was empty.")
    else:
        print("No Woolworths defaultDatasetId found.")
except Exception as e:
    print(f"Woolworths error: {e}")

print("\n" + "="*50 + "\n")

# 3. Coles Scraper run
coles_input = {
    'urls': ['https://www.coles.com.au/search/products?q=milk'],
    'ignore_url_failures': True,
    'max_items_per_url': 1,
    'proxy': {
        'useApifyProxy': True,
        'apifyProxyGroups': ['RESIDENTIAL']
    }
}

print("Running Coles scraper...")
try:
    coles_run = client.actor('stealth_mode/coles-product-search-scraper').call(
        run_input=coles_input,
        wait_duration=timedelta(seconds=40)
    )
    coles_dataset_id = coles_run.get("defaultDatasetId") if isinstance(coles_run, dict) else getattr(coles_run, "default_dataset_id", None)
    
    if coles_dataset_id:
        print(f"Coles Dataset ID: {coles_dataset_id}")
        coles_items = client.dataset(coles_dataset_id).list_items().items
        print(f"Coles returned {len(coles_items)} dataset items.")
        if coles_items:
            # "print its first dataset item's top-level keys and the first entry of any nested list field containing price info."
            first_item = coles_items[0]
            print("=== Coles First Dataset Item Top-Level Keys ===")
            print(f"Keys: {list(first_item.keys())}")
            
            print("=== Coles First Entry of Nested Lists containing price info ===")
            for k, v in first_item.items():
                if isinstance(v, list) and len(v) > 0:
                    # Let's see if any element in the list contains "price" or similar field. Or just check all nested list fields.
                    # Or let's print the first element of any list field found in top level to be sure we capture potential price info.
                    sample_str = json.dumps(v[0]).lower()
                    if "price" in sample_str or "val" in sample_str or "amt" in sample_str or True: # Keep it general
                        print(f"Nested list field: '{k}' (contains price/info). First entry:")
                        print(json.dumps(v[0], indent=2))
            
            # Also let's print the entire JSON of the first item but truncate long list fields to just the first item, just in case
            full_item_truncated = {}
            for k, v in first_item.items():
                if isinstance(v, list):
                    if len(v) > 0:
                        full_item_truncated[k] = [v[0]]
                    else:
                        full_item_truncated[k] = []
                else:
                    full_item_truncated[k] = v
            print("=== Truncated Coles Dataset Item (first entry for each list) ===")
            print(json.dumps(full_item_truncated, indent=2))
        else:
            print("Coles dataset was empty.")
    else:
        print("No Coles defaultDatasetId found.")
except Exception as e:
    print(f"Coles error: {e}")

