import requests
import re

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
}

# Search all chunks for the actual baseURL pattern or how it constructs queries.
# Let's search pages/_app-60b79218e8a34746.js for the API constructor or any use of NEXT_PUBLIC_METCASH_API_URL
url = "https://www.igashop.com.au/_next/static/chunks/pages/_app-60b79218e8a34746.js"
resp = requests.get(url, headers=headers)
content = resp.text

for m in re.finditer(r'NEXT_PUBLIC_METCASH_API_URL', content):
    pos = m.start()
    print(f"Context around NEXT_PUBLIC_METCASH_API_URL:")
    print(repr(content[pos-100:pos+300]))

