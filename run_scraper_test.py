import tomllib
from modules.pricing import PriceScraper

# Read secrets
with open(".streamlit/secrets.toml", "rb") as f:
    secrets = tomllib.load(f)

zenrows_key = secrets["ZENROWS_KEY"]

# Initialize scraper with empty apify_token and zenrows_key
scraper = PriceScraper("", zenrows_key)

# Call get_live_price_result for Aldi, Arnotts Tim Tam
res1 = scraper.get_live_price_result("Aldi", "Arnotts Tim Tam")
print("RESULT 1:", res1)

# Call get_live_price_result for Aldi, A2 Milk full cream 2L
res2 = scraper.get_live_price_result("Aldi", "A2 Milk full cream 2L")
print("RESULT 2:", res2)
