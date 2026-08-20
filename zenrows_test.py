import tomllib
import time
import requests

# Load ZENROWS_KEY from secrets.toml
with open(".streamlit/secrets.toml", "rb") as f:
    secrets = tomllib.load(f)

# Retrieve ZENROWS_KEY (check nested structure if needed, or dict directly)
# Typically streamlit secrets has a structure or flat keys
zenrows_key = secrets.get("ZENROWS_KEY") or secrets.get("zenrows", {}).get("ZENROWS_KEY") or secrets.get("secrets", {}).get("ZENROWS_KEY")

if not zenrows_key:
    # Try all keys to find ZENROWS_KEY
    def find_key(d, key):
        if key in d:
            return d[key]
        for k, v in d.items():
            if isinstance(v, dict):
                res = find_key(v, key)
                if res:
                    return res
        return None
    zenrows_key = find_key(secrets, "ZENROWS_KEY")

if not zenrows_key:
    raise ValueError("ZENROWS_KEY not found in secrets.toml")

url = "https://www.aldi.com.au/results?q=arnotts%20bbq%20shapes"
params = {
    "apikey": zenrows_key,
    "url": url,
    "js_render": "true",
    "antibot": "true",
    "premium_proxy": "true",
    "block_resources": "image,media,stylesheet,font",
    "wait_for": "#product-listing-grid:not(:has([data-skeleton]))",
    "wait": "5000"
}

print("Sending API request to ZenRows...")
start_time = time.time()
try:
    response = requests.get("https://api.zenrows.com/v1/", params=params, timeout=60)
    elapsed = time.time() - start_time
    
    print(f"Status Code: {response.status_code}")
    print(f"Response Length: {len(response.text)}")
    print(f"Elapsed Time: {elapsed:.2f} seconds")
    
    has_target_price = "$3.99" in response.text
    has_grid = "product-listing-grid" in response.text
    
    print(f"Contains '$3.99': {has_target_price}")
    print(f"Contains 'product-listing-grid': {has_grid}")
except Exception as e:
    elapsed = time.time() - start_time
    print(f"Request failed in {elapsed:.2f} seconds.")
    print(f"Error: {e}")
