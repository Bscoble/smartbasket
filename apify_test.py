import os
import json
from datetime import timedelta
from apify_client import ApifyClient

# Read APIFY_TOKEN
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

print("Parsed token successfully (not printing actual token value)")

client = ApifyClient(token)

run_input = {
    'urls': ['https://www.woolworths.com.au/shop/search/products?searchTerm=milk'],
    'ignore_url_failures': True,
    'max_items_per_url': 1,
    'proxy': {
        'useApifyProxy': True,
        'apifyProxyGroups': ['RESIDENTIAL']
    }
}

try:
    print("Running crawler...")
    run = client.actor('stealth_mode/woolworths-product-search-scraper').call(
        run_input=run_input,
        wait_duration=timedelta(seconds=40)
    )
    # The dictionary keys are under python client naming conventions or raw API. 
    # Let's inspect run object using getattr / vars / keys.
    status = run.get("status") if isinstance(run, dict) else getattr(run, "status", None)
    dataset_id = run.get("defaultDatasetId") if isinstance(run, dict) else getattr(run, "default_dataset_id", None)
    
    print("Run Status:", status)
    if dataset_id:
        print(f"Fetching dataset item(s) from: {dataset_id}")
        items = client.dataset(dataset_id).list_items().items
        print(f"Found {len(items)} items.")
        print("First 2 items:")
        print(json.dumps(items[:2], indent=2))
    else:
        print("No defaultDatasetId/default_dataset_id found in run result.")
        print("Run attributes:", dir(run))
except Exception as e:
    print("An error occurred:")
    import traceback
    traceback.print_exc()
