import requests
import re

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
}

# List of chunks we obtained
chunks = [
    "291-30cf2a6f8733edbf.js", "4713-3ba3ed268fe8b06a.js", "1857-fb95e1109dcad30f.js",
    "1329-31f3f784b1d61040.js", "main-1a2d5e051a5fe5ae.js", "4426-5b5ed9b651a5fc7e.js",
    "polyfills-42372ed130431b0a.js", "894-6650ba76e5aa5c90.js", "4851-7957ee2206a728cc.js",
    "7606-336fe4a4902d422b.js", "8265-2adf143769fd2b83.js", "5205-7631ef70dcf24d80.js",
    "9609-bf8959e08c2cb187.js", "5676-ee8fd48926c0903d.js", "7867-6d7e6c49561940b3.js",
    "framework-bdaa23c9b4a3fbb6.js", "2870-e78bf2276816629f.js", "7692-65494c9ea6ea2ce2.js",
    "pages/_app-60b79218e8a34746.js", "pages/index-8eeddf745bd55fc9.js", "7641-d6692df9b69896ce.js",
    "3933-0adc63eee319bf78.js", "5189-2ccc15bd11a04bc7.js", "webpack-bdf47a2d2908763b.js",
    "2193-e34285b786ebb6de.js", "625-c8e54fb2cc8959c6.js", "5556-fe72918acc03559e.js"
]

substrings = ['nearby', 'store-locator', 'storeLocator', 'findStores', '/stores', 'graphql', 'retailerStoreId', 'postcode', 'suburb', 'latitude', 'longitude']

for chunk in chunks:
    url = f"https://www.igashop.com.au/_next/static/chunks/{chunk}"
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        content = resp.text
        for sub in substrings:
            matches = list(re.finditer(re.escape(sub), content, re.IGNORECASE))
            if matches:
                print(f"\nChunk: {chunk} - Found {len(matches)} matches for '{sub}'")
                for i, match in enumerate(matches[:2]): # show up to 2 matches to keep output size manageable
                    start = max(0, match.start() - 200)
                    end = min(len(content), match.end() + 200)
                    print(f"Match {i+1} at offset {match.start()}: {repr(content[start:end])}")
    except Exception as e:
        print(f"Error fetching/searching chunk {chunk}: {e}")

