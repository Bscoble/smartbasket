import os
import json
from apify_client import ApifyClient

token = None
with open(".streamlit/secrets.toml", "r") as f:
    for line in f:
        if "APIFY_TOKEN" in line:
            token = line.split("=", 1)[1].strip().strip('"').strip("'")

client = ApifyClient(token)
runs = client.actor('stealth_mode/woolworths-product-search-scraper').runs().list().items
if runs:
    last_run = runs[0]
    dataset_id = getattr(last_run, 'default_dataset_id', None) or last_run.get('defaultDatasetId')
    items = client.dataset(dataset_id).list_items().items
    if items:
        prod = items[0]['products'][0] if 'products' in items[0] and items[0]['products'] else items[0]
        # print specific interesting fields
        print(json.dumps({k: v for k, v in prod.items() if 'price' in k.lower() or k in ['name', 'stockcode', 'barcode']}, indent=2))
