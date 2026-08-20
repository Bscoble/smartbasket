import requests
import re

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
}

# Let's search pages/_app-60b79218e8a34746.js (main app bundle) or other chunks for api endpoints/base url
url = "https://www.igashop.com.au/_next/static/chunks/3933-0adc63eee319bf78.js"
resp = requests.get(url, headers=headers)
content = resp.text

# search for things like 'baseURL', 'api/', 'api_url', 'basePath'
print("matches for basePath, baseUrl, api:")
for m in re.finditer(r'(baseURL|basePath|api_url|api/|/api)[^"\']{0,50}', content, re.IGNORECASE):
    print(repr(content[max(0, m.start()-50):min(len(content), m.end()+100)]))

