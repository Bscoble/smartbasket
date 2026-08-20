import requests
import re

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
}

url = "https://www.igashop.com.au/_next/static/chunks/pages/_app-60b79218e8a34746.js"
resp = requests.get(url, headers=headers)
content = resp.text

print("Length of app bundle:", len(content))

# Look for standard hostnames, NEXT_PUBLIC, API configurations, or axios/fetch.get patterns
# Let's search for NEXT_PUBLIC
print("\nNEXT_PUBLIC instances:")
for m in re.finditer(r'NEXT_PUBLIC_[A-Z0-9_]*', content):
    pos = m.start()
    print(content[pos:pos+100])

print("\nGET/POST paths:")
# match patterns like .get("/...") or .post("/...")
paths = set(re.findall(r'\.(?:get|post|put|delete)\(["\'](/[^"\']+)["\']', content))
for p in sorted(list(paths))[:30]:
    print(p)
