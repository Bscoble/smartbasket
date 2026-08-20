import tomllib
import requests
from bs4 import BeautifulSoup

# Load ZENROWS_KEY using tomllib
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

print("Initiating Zenrows request...")
try:
    response = requests.get('https://api.zenrows.com/v1/', params=params, timeout=40)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, 'html.parser')
    
    # Find matching elements
    cards = soup.select("[data-product-card='true']")
    print(f"Total element count matching [data-product-card='true']: {len(cards)}")
    
    # Print the first 3 cards info
    for i, card in enumerate(cards[:3]):
        tag_name = card.name
        class_names = card.get('class', [])
        text_collapsed = " ".join(card.get_text().split())[:500]
        
        print(f"\nCard {i + 1}:")
        print(f"Tag Name: {tag_name}")
        print(f"Class Names: {class_names}")
        print(f"Collapsed Text: {text_collapsed}")

except Exception as e:
    print(f"An error occurred: {e}")
