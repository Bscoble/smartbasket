import tomllib
import requests
from bs4 import BeautifulSoup

with open(".streamlit/secrets.toml", "rb") as f:
    secrets = tomllib.load(f)
zenrows_key = secrets["ZENROWS_KEY"]

url = "https://www.aldi.com.au/results?q=arnotts%20bbq%20shapes"
params = {
    'apikey': zenrows_key,
    'url': url,
    'js_render': 'true',
    'wait': '5000',
    'antibot': 'true',
    'premium_proxy': 'true',
    'block_resources': 'image,media,stylesheet,font'
}

print("Sending request to Zenrows...")
try:
    response = requests.get('https://api.zenrows.com/v1/', params=params, timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, 'html.parser')
    
    # Print some tags, classes, or check for common class names
    print("Page Title:", soup.title.string if soup.title else "No title")
    
    # Find any links, forms, common class containers
    # Aldi commonly uses div with class "m-results-filter" or "box--wrapper" or "m-product-grid" or "box--product" or similar.
    # Let's search for classes containing 'product'
    product_related = []
    for tag in soup.find_all(class_=True):
        for cls in tag['class']:
            if 'product' in cls.lower() or 'result' in cls.lower() or 'card' in cls.lower() or 'item' in cls.lower():
                product_related.append((tag.name, cls, len(tag.get_text().strip())))
    
    print(f"Product related elements count: {len(product_related)}")
    for tag_name, cls, text_len in product_related[:20]:
        print(f"  <{tag_name} class='{cls}'> (Text length: {text_len})")
        
    # Also find all elements with an href containing 'product' or similar
    prod_links = soup.find_all('a', href=True)
    print(f"Total links: {len(prod_links)}")
    for l in prod_links[:10]:
        print(f"  Link: {l['href']} Text: {l.get_text().strip()[:100]}")

except Exception as e:
    print(f"Error occurred: {e}")
