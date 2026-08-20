import re

# Let's load the chunk and app bundle file. Wait, let's just make a script that downloads them and does deep searching.
import requests
import tomllib

with open('.streamlit/secrets.toml', 'rb') as f:
    secrets = tomllib.load(f)
zenrows_key = secrets['ZENROWS_KEY']

def make_zenrows_request(url, params=None):
    zenrows_url = "https://api.zenrows.com/v1/"
    query_params = {'apikey': zenrows_key, 'url': url}
    if params:
        query_params.update(params)
    return requests.get(zenrows_url, params=query_params)

# We know the URLs:
chunk_url = 'https://www.igashop.com.au/_next/static/chunks/3933-0adc63eee319bf78.js'
app_js_url = 'https://www.igashop.com.au/_next/static/chunks/pages/_app-60b79218e8a34746.js'

print("Downloading chunk...")
chunk = make_zenrows_request(chunk_url, {'premium_proxy': 'true', 'antibot': 'true'}).text
print("Downloading app bundle...")
app_bundle = make_zenrows_request(app_js_url, {'premium_proxy': 'true', 'antibot': 'true'}).text

def search_term(text, name, term, size=500):
    print(f"\n===== Searching '{term}' in {name} =====")
    for m in re.finditer(re.escape(term), text, re.IGNORECASE):
        pos = m.start()
        start = max(0, pos - size)
        end = min(len(text), pos + len(term) + size)
        print(f"Match at {pos}:")
        print(text[start:end])
        print("-" * 50)

# We want to find how stores are listed. Let's search for "stores" in both.
search_term(app_bundle, "app bundle", "/stores", size=200)
search_term(chunk, "chunk 3933", "/stores", size=300)
search_term(chunk, "chunk 3933", "postCode", size=350)
search_term(chunk, "chunk 3933", "latitude", size=350)
