"""
One-time backfill script to populate the Standard Prices reference table.

Run this locally (not in CI) after adding ZENROWS_KEY, APIFY_TOKEN, and
gcp_service_account to .streamlit/secrets.toml:

    python backfill_standard_prices.py

It reuses the same PriceScraper/SheetsManager classes as the live app, so
results benefit from the existing relevance filtering and query retries
instead of duplicating scraping logic.
"""

import tomllib
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials

from config import SPREADSHEET_ID, GOOGLE_SCOPES
from modules.brands import merge_brand_metadata
from modules.pricing import PriceScraper
from modules.sheets import SheetsManager

# 350 common Australian grocery items, grouped by category for maintainability.
STAPLES = [
    # Dairy & Eggs
    "Full Cream Milk 2L", "Skim Milk 2L", "Lite Milk 2L", "Almond Milk 1L", "Soy Milk 1L",
    "Oat Milk 1L", "Butter 500g", "Margarine 500g", "Cheddar Cheese Block 500g",
    "Tasty Cheese Slices 250g", "Mozzarella Cheese Block 500g", "Cream Cheese 250g",
    "Sour Cream 300g", "Greek Yogurt 1kg", "Vanilla Yogurt 4pk", "Free Range Eggs 12pk",
    "Free Range Eggs 6pk", "Cage Eggs 12pk", "Thickened Cream 300ml", "Custard 1L",
    # Bread & Bakery
    "White Bread 700g", "Wholemeal Bread 700g", "Multigrain Bread 700g", "Sourdough Loaf",
    "White Bread Rolls 6pk", "Wholemeal Wraps 8pk", "English Muffins 6pk", "Crumpets 6pk",
    "Fruit Buns 6pk", "Bagels 4pk", "Pita Bread 6pk", "Garlic Bread 2pk", "Croissants 4pk",
    "Hot Cross Buns 6pk", "Rye Bread 500g",
    # Meat & Seafood
    "Chicken Breast 1kg", "Chicken Thigh Fillets 1kg", "Chicken Tenders 500g",
    "Chicken Drumsticks 1kg", "Whole Chicken 1.6kg", "Beef Mince 500g", "Beef Rump Steak 500g",
    "Beef Sausages 500g", "Beef Sausages 6pk", "Pork Sausages 500g", "Bacon Rashers 500g",
    "Lamb Chops 500g", "Lamb Mince 500g", "Ham Slices 200g", "Salami 100g", "Chorizo 200g",
    "Frozen Prawns 500g", "Salmon Fillets 500g", "Tuna in Springwater 425g", "Smoked Salmon 100g",
    "Fish Fingers 400g", "Crumbed Fish Fillets 400g", "Meatballs 500g", "Chicken Nuggets 500g",
    "Chicken Schnitzel 500g",
    # Fruit & Veg
    "Bananas", "Apples 1kg", "Red Apples 1kg", "Green Apples 1kg", "Oranges 1kg", "Mandarins 1kg",
    "Grapes 500g", "Strawberries 250g", "Blueberries 125g", "Watermelon Whole", "Lemons 1kg",
    "Avocados 3pk", "Tomatoes 1kg", "Cherry Tomatoes 250g", "Cucumber", "Carrots 1kg",
    "Potatoes 2kg", "Sweet Potato 1kg", "Onions Brown 1kg", "Onions Red 1kg", "Garlic 250g",
    "Broccoli", "Cauliflower", "Capsicum Red", "Capsicum Green", "Mushrooms 200g",
    "Lettuce Iceberg", "Baby Spinach 120g", "Zucchini 500g", "Pumpkin Butternut",
    # Pantry Staples
    "White Sugar 1kg", "Brown Sugar 1kg", "Plain Flour 1kg", "Self Raising Flour 1kg",
    "Rice White 1kg", "Rice Brown 1kg", "Basmati Rice 1kg", "Pasta Spaghetti 500g",
    "Pasta Penne 500g", "Pasta Fusilli 500g", "Pasta Sauce Napoletana 500g",
    "Pasta Sauce Bolognese 500g", "Canned Tomatoes 400g", "Canned Chickpeas 400g",
    "Canned Kidney Beans 400g", "Canned Corn 420g", "Canned Tuna 185g", "Canned Baked Beans 420g",
    "Peanut Butter 500g", "Vegemite 220g", "Honey 500g", "Jam Strawberry 500g", "Nutella 400g",
    "Olive Oil 500ml", "Vegetable Oil 750ml", "Canola Oil 750ml", "Soy Sauce 250ml",
    "Tomato Sauce 500ml", "BBQ Sauce 500ml", "Mayonnaise 500g", "Mustard 250g", "Salt 500g",
    "Black Pepper 100g", "Stock Cubes Chicken 12pk", "Gravy Powder 200g",
    # Cereal & Breakfast
    "Weet-Bix 750g", "Corn Flakes 500g", "Just Right 500g", "Nutri-Grain 500g", "Coco Pops 500g",
    "Sultana Bran 500g", "Rolled Oats 1kg", "Muesli 750g", "Granola 500g", "Pancake Mix 500g",
    "Maple Syrup 375ml", "Instant Oatmeal Sachets 8pk", "Rice Bubbles 500g", "Special K 500g",
    "Cruskits 250g",
    # Snacks & Confectionery
    "Tim Tam Original 200g", "Tim Tam Dark 200g", "BBQ Shapes 175g", "Pizza Shapes 175g",
    "Cheese and Bacon Shapes 175g", "Cheese Twists 200g", "Doritos Cheese Supreme 170g",
    "Doritos Corn Chips 170g", "Smiths Crinkle Cut Chips 170g", "Kettle Chips Sea Salt 165g",
    "Pretzels 300g", "Freddo Frogs", "Cadbury Dairy Milk 180g", "Kit Kat 4 Finger",
    "Mars Bar 47g", "Snickers 50g", "Maltesers 130g", "M&Ms Chocolate 150g",
    "Allens Party Mix 750g", "Twisties Cheese 270g", "Popcorn Butter 100g",
    "Chocolate Biscuits 200g", "Scotch Finger Biscuits 250g", "Tim Tam Slam Pack",
    "Shortbread Biscuits 250g", "Arnotts Assorted Cream 500g", "Vita-Weat Crackers 250g",
    "Jatz Crackers 225g", "Rice Crackers 100g", "Muesli Bars 6pk", "Roll Ups Fruit 5pk",
    "LCMs 6pk", "Nutri-Grain Bars 6pk", "Fruit Snacks 5pk", "Corn Chips Salsa 500g",
    # Beverages
    "Coca-Cola 2L", "Coca-Cola No Sugar 2L", "Sprite 2L", "Fanta 2L", "Pepsi 2L",
    "Solo Lemonade 1.25L", "Orange Juice 2L", "Apple Juice 2L", "Tropical Juice 2L",
    "Sparkling Water 1.25L", "Still Water 1.5L", "Water 24pk 600ml", "Gatorade 600ml",
    "Powerade 600ml", "Red Bull 250ml", "V Energy Drink 250ml", "Instant Coffee 200g",
    "Ground Coffee 200g", "Coffee Pods 10pk", "Tea Bags Black 100pk", "Green Tea Bags 25pk",
    "Hot Chocolate 450g", "Milo 450g", "Cordial Orange 1L", "Cordial Lemon 1L",
    "Long Black Coffee Cans 6pk", "Iced Coffee 500ml", "Soda Water 1.25L", "Ginger Beer 1.25L",
    "Apple Cider Vinegar 500ml",
    # Frozen
    "Frozen Peas 500g", "Frozen Mixed Vegetables 500g", "Frozen Corn 500g", "Frozen Chips 1kg",
    "Frozen Wedges 1kg", "Frozen Pizza Margherita", "Frozen Pizza Supreme",
    "Frozen Lasagne 500g", "Frozen Dumplings 500g", "Ice Cream Vanilla 2L",
    "Ice Cream Chocolate 2L", "Ice Cream Neapolitan 2L", "Icy Poles 12pk", "Frozen Berries 500g",
    "Frozen Mango 500g", "Frozen Spring Rolls 500g", "Frozen Fish Fillets 500g",
    "Frozen Meat Pies 4pk", "Frozen Sausage Rolls 12pk", "Frozen Garlic Bread 2pk",
    # Household & Cleaning
    "Dishwashing Liquid 500ml", "Dishwasher Tablets 30pk", "Laundry Powder 2kg",
    "Laundry Liquid 2L", "Fabric Softener 2L", "Multi-Purpose Spray 750ml",
    "Glass Cleaner 750ml", "Toilet Cleaner 500ml", "Bleach 2L", "Paper Towel 4pk",
    "Toilet Paper 12pk", "Tissues 4pk", "Bin Liners 30L 20pk", "Bin Liners 60L 20pk",
    "Aluminium Foil 30m", "Cling Wrap 30m", "Snap Lock Bags 50pk", "Sponges 6pk",
    "Rubber Gloves", "Air Freshener Spray", "Insect Spray 300g", "Rat Bait Station",
    "Fly Spray 300g", "Surface Wipes 80pk", "Oven Cleaner 500ml", "Floor Cleaner 1L",
    "Mop Refill", "Dish Brush", "Steel Wool Pads 10pk", "Handwash 500ml",
    # Personal Care
    "Shampoo 400ml", "Conditioner 400ml", "Body Wash 500ml", "Bar Soap 4pk", "Toothpaste 110g",
    "Toothbrush 2pk", "Mouthwash 500ml", "Deodorant Spray 150ml", "Deodorant Roll-On 50ml",
    "Razors 4pk", "Shaving Cream 200g", "Cotton Buds 200pk", "Cotton Balls 100pk",
    "Hand Sanitiser 500ml", "Sunscreen SPF50 200ml", "Moisturiser 200ml", "Lip Balm",
    "Hair Gel 200g", "Hair Spray 300g", "Panty Liners 30pk", "Pads Regular 14pk",
    "Tampons Regular 16pk", "Nappies Size 4 40pk", "Baby Wipes 80pk", "Baby Shampoo 200ml",
    "Talcum Powder 200g", "Foot Powder 100g", "Nail Polish Remover 200ml", "Cotton Pads 80pk",
    "Wet Wipes 80pk",
    # Baking
    "Baking Powder 100g", "Bicarb Soda 200g", "Vanilla Essence 100ml", "Cocoa Powder 250g",
    "Icing Sugar 500g", "Caster Sugar 1kg", "Choc Chips 200g", "Desiccated Coconut 200g",
    "Cake Mix Chocolate 340g", "Cake Mix Vanilla 340g", "Muffin Mix 400g", "Yeast Sachets 3pk",
    "Almond Meal 500g", "Rice Paper 100g", "Gelatine Powder 100g", "Food Colouring 4pk",
    "Sprinkles 100g", "Pastry Sheets 2pk", "Pizza Base 2pk", "Breadcrumbs 250g",
    # Deli & International
    "Hummus 200g", "Tzatziki 200g", "Guacamole 200g", "Salsa Dip 250g", "French Onion Dip 200g",
    "Antipasto Mix 250g", "Olives Kalamata 250g", "Feta Cheese 200g", "Haloumi 180g",
    "Prosciutto 100g", "Pesto 190g", "Curry Paste Red 200g", "Curry Paste Green 200g",
    "Coconut Milk 400ml", "Coconut Cream 400ml", "Fish Sauce 200ml", "Oyster Sauce 250ml",
    "Hoisin Sauce 250ml", "Sweet Chilli Sauce 250ml", "Sriracha Sauce 250ml", "Taco Kit",
    "Nachos Kit", "Wonton Wrappers 250g", "Rice Noodles 200g", "Udon Noodles 200g",
    # Condiments & Extra Pantry
    "Balsamic Vinegar 250ml", "White Vinegar 500ml", "Worcestershire Sauce 250ml",
    "Chilli Flakes 50g", "Paprika 50g", "Cumin Ground 50g", "Cinnamon Ground 50g",
    "Mixed Herbs 25g", "Oregano Dried 15g", "Basil Dried 15g", "Garlic Powder 100g",
    "Onion Powder 100g", "Sesame Oil 250ml", "Rice Wine Vinegar 250ml", "Panko Breadcrumbs 250g",
    "Tahini 375g", "Peanut Oil 750ml", "Cornflour 500g", "Custard Powder 300g",
    "Gravy Mix Beef 200g",
]

STORES = ["Woolworths", "Coles", "Aldi"]

# Save progress this often so a 1,000+ scrape run can be interrupted and resumed.
SAVE_EVERY_N_ITEMS = 20


def load_secrets() -> dict:
    with open(".streamlit/secrets.toml", "rb") as f:
        return tomllib.load(f)


def build_sheets_manager(secrets: dict) -> SheetsManager:
    creds = Credentials.from_service_account_info(
        secrets["gcp_service_account"], scopes=GOOGLE_SCOPES
    )
    gc = gspread.authorize(creds)
    spreadsheet = gc.open_by_key(SPREADSHEET_ID)
    return SheetsManager(spreadsheet)


def backfill() -> None:
    secrets = load_secrets()
    scraper = PriceScraper(secrets.get("APIFY_TOKEN", ""), secrets.get("ZENROWS_KEY", ""))
    sheets_manager = build_sheets_manager(secrets)

    standard_prices = sheets_manager.load_standard_prices()
    succeeded = 0
    failed = 0
    skipped = 0

    for idx, item_name in enumerate(STAPLES, start=1):
        item_lower = item_name.lower()
        print(f"[{idx}/{len(STAPLES)}] {item_name}")
        for store in STORES:
            key = (store, item_lower)
            existing = standard_prices.get(key)
            if existing and sheets_manager.is_standard_price_valid(existing):
                skipped += 1
                continue

            result = scraper.get_live_price_result(store, item_name)
            price = result.get("price")
            if price is not None:
                existing = standard_prices.get(key, {})
                standard_prices[key] = {
                    **existing,
                    "price": price,
                    "product_name": result.get("product_name") or item_name,
                    "last_verified": datetime.now(),
                    **merge_brand_metadata(existing, result),
                    "barcode": result.get("barcode") or existing.get("barcode", ""),
                    "source_url": result.get("source_url") or existing.get("source_url", ""),
                }
                succeeded += 1
                print(f"  OK    {store:<12} ${price:.2f}")
            else:
                failed += 1
                print(f"  MISS  {store:<12} {result.get('message', 'unavailable')}")

        if idx % SAVE_EVERY_N_ITEMS == 0:
            sheets_manager.save_standard_prices(standard_prices)
            print(f"  -- checkpoint saved at item {idx}/{len(STAPLES)} --")

    sheets_manager.save_standard_prices(standard_prices)
    print(
        f"\nDone. {succeeded} priced, {failed} missing, {skipped} already fresh. "
        f"{len(standard_prices)} entries saved."
    )


if __name__ == "__main__":
    backfill()
