import requests
import re

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
}

url = "https://www.igashop.com.au/"
resp = requests.get(url, headers=headers)
print("Main page fetched, status:", resp.status_code)
# find all /_next/static/chunks/ JS files
chunks = re.findall(r'/_next/static/chunks/[^"]+\.js', resp.text)
for chunk in set(chunks):
    print("Chunk found:", chunk)

