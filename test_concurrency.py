import tomllib
import time
import concurrent.futures
from modules.pricing import PriceScraper

# Read secrets
with open(".streamlit/secrets.toml", "rb") as f:
    secrets = tomllib.load(f)

apify_token = secrets["APIFY_TOKEN"]
zenrows_key = secrets["ZENROWS_KEY"]

assert apify_token and zenrows_key, "Secrets not loaded correctly!"
print("Secrets loaded successfully without printing them.")

scraper = PriceScraper(apify_token, zenrows_key)

pairs = [
    ("Aldi", "A2 Milk full cream 2L"),
    ("Aldi", "Arnott's Bbq shapes"),
    ("Coles", "A2 Milk full cream 2L"),
    ("Coles", "Arnott's Bbq shapes"),
    ("Woolworths", "A2 Milk full cream 2L"),
    ("Woolworths", "Arnott's Bbq shapes")
]

def task(store, item_name):
    t0 = time.time()
    result = scraper.get_live_price_result(store, item_name)
    elapsed = time.time() - t0
    return store, item_name, result, elapsed

print("Starting concurrent execution using ThreadPoolExecutor(max_workers=3)...")
total_start = time.time()

with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
    futures = [executor.submit(task, store, item_name) for store, item_name in pairs]
    for future in concurrent.futures.as_completed(futures):
        store, item_name, result, elapsed = future.result()
        print(f"Result for {store} / '{item_name}': {result} | Elapsed: {elapsed:.2f}s")

total_elapsed = time.time() - total_start
print(f"Total overall elapsed time: {total_elapsed:.2f}s")
