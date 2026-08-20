import tomllib
from modules.pricing import PriceScraper

# Read key using tomllib
with open(".streamlit/secrets.toml", "rb") as f:
    secrets = tomllib.load(f)

zenrows_key = secrets["ZENROWS_KEY"]

# Create PriceScraper with empty apify_token and zenrows_key
scraper = PriceScraper("", zenrows_key)

# Call structure results and print both
res1 = scraper.get_live_price_result('Aldi', 'Arnotts Bbq shapes')
res2 = scraper.get_live_price_result('Aldi', 'A2 Milk full cream 2L')

print("RESULT1:", res1)
print("RESULT2:", res2)
