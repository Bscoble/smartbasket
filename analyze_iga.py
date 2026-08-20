import requests
import tomllib
from bs4 import BeautifulSoup
import re

with open('.streamlit/secrets.toml', 'rb') as f:
    secrets = tomllib.load(f)
zenrows_key = secrets['ZENROWS_KEY']

def make_zenrows_request(url, params=None):
    zenrows_url = "https://api.zenrows.com/v1/"
    query_params = {
        'apikey': zenrows_key,
        'url': url,
    }
    if params:
        query_params.update(params)
    resp = requests.get(zenrows_url, params=query_params)
    return resp

print("Retrieving homepage...")
homepage_params = {
    'js_render': 'true',
    'wait': '5000',
    'premium_proxy': 'true',
    'antibot': 'true'
}
resp_home = make_zenrows_request('https://www.igashop.com.au/', homepage_params)
soup = BeautifulSoup(resp_home.text, 'html.parser')

scripts = soup.find_all('script')
app_js_url = None
for s in scripts:
    src = s.get('src', '')
    if 'pages/_app' in src:
        if src.startswith('/'):
            app_js_url = 'https://www.igashop.com.au' + src
        else:
            app_js_url = src
        break

if not app_js_url:
    print("Could not find _app.js directly in script src, trying to search via regex in html text...")
    match = re.search(r'/_next/static/chunks/pages/_app-[a-f0-9]+\.js', resp_home.text)
    if match:
        app_js_url = 'https://www.igashop.com.au' + match.group(0)

print(f"Discovered app JS URL: {app_js_url}")

# Download both chunk 3933 and the _app JS
chunk_url = 'https://www.igashop.com.au/_next/static/chunks/3933-0adc63eee319bf78.js'
print(f"Fetching chunk {chunk_url}...")
resp_chunk = make_zenrows_request(chunk_url, {'premium_proxy': 'true', 'antibot': 'true'})
chunk_content = resp_chunk.text

app_content = ""
if app_js_url:
    print(f"Fetching app bundle {app_js_url}...")
    resp_app = make_zenrows_request(app_js_url, {'premium_proxy': 'true', 'antibot': 'true'})
    app_content = resp_app.text
else:
    print("Error: App bundle URL not found!")

# Now search both files for 'zipcodes' and print contexts
def search_and_print(content, name, term, limit=8, context_len=600):
    print(f"\n--- Searching for '{term}' in {name} ---")
    matches = list(re.finditer(re.escape(term), content, re.IGNORECASE))
    print(f"Found {len(matches)} occurrences.")
    for i, m in enumerate(matches[:limit]):
        pos = m.start()
        start = max(0, pos - context_len // 2)
        end = min(len(content), pos + context_len // 2)
        print(f"Occurrence {i+1} at index {pos}:")
        print(content[start:end])
        print("-" * 40)

search_and_print(chunk_content, '3933 chunk', 'zipcodes')
if app_content:
    search_and_print(app_content, 'app bundle', 'zipcodes')

# Search files for other relevant fields
search_and_print(chunk_content, '3933 chunk', 'retailerStoreId')
search_and_print(chunk_content, '3933 chunk', '/select')

if app_content:
    search_and_print(app_content, 'app bundle', 'retailerStoreId')
    search_and_print(app_content, 'app bundle', '/select')

# Finally, query the API using direct requests.get
print("\n--- Live GET request to the API ---")
api_url = "https://z2zelumxs4.execute-api.ap-southeast-2.amazonaws.com/altprod/v1/sessions/zipcodes/2000/stores"
try:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    }
    api_resp = requests.get(api_url, headers=headers)
    print(f"Status Code: {api_resp.status_code}")
    print(f"Response Body (first 1500 chars):\n{api_resp.text[:1500]}")
except Exception as e:
    print(f"Error querying API: {e}")
