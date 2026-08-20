import tomllib
import requests
from bs4 import BeautifulSoup

# Read ZENROWS_KEY
with open(".streamlit/secrets.toml", "rb") as f:
    secrets = tomllib.load(f)
zenrows_key = secrets["ZENROWS_KEY"]

url = "https://www.aldi.com.au/results?q=arnotts%20bbq%20shapes"
# Zenrows API parameters
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
    print(f"Request successful. Status code: {response.status_code}")
    
    soup = BeautifulSoup(response.content, 'html.parser')
    cards = soup.select("[data-product-card='true']")
    print(f"Found {len(cards)} element(s) matching [data-product-card='true']")
    
    for i, card in enumerate(cards[:3]):
        tag_name = card.name
        classes = card.get('class', [])
        text = ' '.join(card.get_text().split())[:500]
        print(f"\n--- Card {i+1} ---")
        print(f"Tag: {tag_name}")
        print(f"Classes: {classes}")
        print(f"Text (max 500 chars): {text}")
        
except Exception as e:
    print(f"Error occurred: {e}")
