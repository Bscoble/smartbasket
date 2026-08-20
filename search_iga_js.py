import tomllib
import requests
import json
import re

# Read Zenrows key just in case, but do NOT print it.
try:
    with open(".streamlit/secrets.toml", "rb") as f:
        config = tomllib.load(f)
        zenrows_key = config.get("ZENROWS_KEY")
except Exception as e:
    zenrows_key = None
    print(f"Failed to read ZENROWS_KEY: {e} (continuing anyway)")

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
}

# Fetch JS Chunk URL
js_url = "https://www.igashop.com.au/_next/static/chunks/625-c8e54fb2cc8959c6.js"
print(f"Fetching JS resource: {js_url}")
try:
    response = requests.get(js_url, headers=headers, timeout=20)
    print(f"JS fetch status: {response.status_code}")
    js_content = response.text
except Exception as e:
    print(f"Error fetching JS directly: {e}")
    js_content = ""

# Search substrings
substrings = ['nearby', 'store-locator', 'storeLocator', 'findStores', '/stores', 'graphql', 'retailerStoreId', 'postcode', 'suburb', 'latitude', 'longitude']

for sub in substrings:
    print(f"\n--- Searching for: '{sub}' ---")
    matches = list(re.finditer(re.escape(sub), js_content, re.IGNORECASE))
    print(f"Found {len(matches)} matches.")
    # Show up to 4 matches
    for i, match in enumerate(matches[:4]):
        start = max(0, match.start() - 200)
        end = min(len(js_content), match.end() + 200)
        context = js_content[start:end]
        print(f"Match {i+1} (offset {match.start()}):")
        print(repr(context))

# GraphQL Test
graphql_url = "https://www.igashop.com.au/api/graphql"
graphql_body = {"query": "{__typename}"}
print(f"\nFetching GraphQL structure at: {graphql_url}")
try:
    resp = requests.post(graphql_url, json=graphql_body, headers=headers, timeout=20)
    print(f"GraphQL status code: {resp.status_code}")
    print("GraphQL first 500 chars response:")
    print(resp.text[:500])
except Exception as e:
    print(f"Error testing GraphQL: {e}")
