import tomllib
import time
import requests

# Load ZENROWS_KEY from secrets.toml
with open(".streamlit/secrets.toml", "rb") as f:
    secrets = tomllib.load(f)

# Retrieve ZENROWS_KEY (check nested structure if needed, or dict directly)
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

def make_request(name, extra_params):
    url = "https://www.aldi.com.au/results?q=arnotts%20bbq%20shapes"
    params = {
        "apikey": zenrows_key,
        "url": url,
        "js_render": "true",
        "antibot": "true",
        "premium_proxy": "true",
        "block_resources": "image,media,stylesheet,font",
        "wait": "5000"
    }
    params.update(extra_params)
    
    print(f"--- Request {name} ---")
    start_time = time.time()
    try:
        response = requests.get("https://api.zenrows.com/v1/", params=params, timeout=35)
        elapsed = time.time() - start_time
        print(f"Elapsed: {elapsed:.2f} seconds")
        print(f"Status: {response.status_code}")
        print(f"Length: {len(response.text)}")
        print(f"Contains '$3.99': {'$3.99' in response.text}")
        print(f"Contains 'data-skeleton': {'data-skeleton' in response.text}")
        print(f"Contains 'data-product-card': {'data-product-card' in response.text}")
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"Elapsed: {elapsed:.2f} seconds")
        print(f"Exception: {type(e).__name__}: {str(e)}")

# Request A
make_request("A", {})

# Request B (with wait_for)
make_request("B", {"wait_for": "#product-listing-grid"})
