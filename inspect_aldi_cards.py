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
    
    # Print elements that have any attribute containing 'product-card' or 'product'
    attrs = []
    for tag in soup.find_all(True):
        for attr, val in tag.attrs.items():
            if 'product' in str(attr) or 'product' in str(val) or 'card' in str(attr) or 'card' in str(val):
                attrs.append((tag.name, attr, val))
                
    print(f"Found {len(attrs)} tags matching product/card attributes:")
    for a in attrs[:30]:
        print(a)
        
    # Print divs that have 'card' or 'product' in class
    card_divs = soup.find_all('div', class_=lambda c: c and ('card' in c or 'product' in c))
    print(f"\nFound {len(card_divs)} divs with 'card' or 'product' in class")
    for d in card_divs[:5]:
        print(d.name, d.get('class'), ' '.join(d.get_text().split())[:100])

except Exception as e:
    print(f"Error occurred: {e}")
