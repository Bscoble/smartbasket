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
    
    # search for data-product-card using select
    print("Match counts:")
    print("select([data-product-card]):", len(soup.select("[data-product-card]")))
    print("select([data-product-card=true]):", len(soup.select("[data-product-card=true]")))
    print("select([data-product-card='true']):", len(soup.select("[data-product-card='true']")))
    print("select([data-product-card=\"true\"]):", len(soup.select('[data-product-card="true"]')))
    
    # print all tags that have data-product-card
    tags = soup.find_all(lambda tag: tag.has_attr('data-product-card'))
    print(f"find_all has data-product-card: {len(tags)}")
    for t in tags[:5]:
        print(t.name, t.attrs)
        
    print("\nLet's find any tag that has 'data-' containing 'product'")
    data_tags = soup.find_all(lambda tag: any('product' in attr for attr in tag.attrs if attr.startswith('data-')))
    print(f"Found {len(data_tags)} data-* tags containing 'product'")
    for t in data_tags[:5]:
        print(t.name, t.attrs)

except Exception as e:
    print(f"Error: {e}")
