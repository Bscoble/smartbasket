import json
import time
import requests

# Optional: Try importing rapidfuzz for enhanced fuzzy brand matching
try:
    from rapidfuzz import process, fuzz
    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False


class ProductSearchEngine:
    """
    Search engine that queries the Open Food Facts API with fuzzy brand fallback.
    """
    def __init__(self, user_agent: str = "SmartBasketApp/1.0 (contact@example.com)"):
        self.api_url = "https://world.openfoodfacts.org/cgi/search.pl"
        self.headers = {"User-Agent": user_agent}

    def search_products(self, query: str, limit: int = 5) -> list[dict]:
        """
        Queries Open Food Facts for matching products.
        """
        clean_query = query.strip()
        if not clean_query:
            return []

        params = {
            "search_terms": clean_query,
            "search_simple": 1,
            "action": "process",
            "json": 1,
            "page_size": limit
        }

        try:
            response = requests.get(self.api_url, params=params, headers=self.headers, timeout=5)
            response.raise_for_status()
            data = response.json()

            products = data.get("products", [])
            results = []

            for item in products:
                product_name = item.get("product_name", "").strip() or "Unknown Item"
                brand = item.get("brands", "").strip() or "Generic/Unknown"
                barcode = item.get("code", "")
                image_url = item.get("image_small_url", "")

                results.append({
                    "product_name": product_name,
                    "brand": brand,
                    "barcode": barcode,
                    "image_url": image_url,
                    "display_title": f"{brand} - {product_name}" if brand != "Generic/Unknown" else product_name
                })

            # Optional local fuzzy refinement if rapidfuzz is installed
            if HAS_RAPIDFUZZ and results:
                choices = [r["display_title"] for r in results]
                matches = process.extract(clean_query, choices, scorer=fuzz.WRatio, limit=limit)
                refined_results = [results[idx] for _, _, idx in matches]
                return refined_results

            return results

        except requests.RequestException as err:
            print(f"⚠️ API Lookup Error: {err}")
            return []


class ProductLookupInterface:
    """
    Mock interface simulating real-time autocompletion with a debouncer.
    """
    def __init__(self, debounce_delay: float = 0.3):
        self.engine = ProductSearchEngine()
        self.debounce_delay = debounce_delay
        self.last_typed_time = 0

    def on_text_input(self, text_input: str) -> list[dict]:
        """
        Simulates receiving input events from a text box.
        """
        # Simulate typing debounce delay
        self.last_typed_time = time.time()
        time.sleep(self.debounce_delay)

        print(f"\n🔍 Searching Open Food Facts for: '{text_input}'...")
        suggestions = self.engine.search_products(text_input)
        return suggestions


# ==============================================================================
# Execution / Demonstration
# ==============================================================================
if __name__ == "__main__":
    app = ProductLookupInterface(debounce_delay=0.1)

    # Example test cases including typos and brand names
    test_queries = ["Hillcrest", "arrnots", "Vegemite"]

    for query in test_queries:
        results = app.on_text_input(query)

        if results:
            print(f" Found {len(results)} matches:")
            for idx, item in enumerate(results, start=1):
                print(f"  [{idx}] {item['display_title']}")
                print(f"      • Barcode: {item['barcode']}")
                print(f"      • Image:   {item['image_url'] or 'N/A'}")
        else:
            print("  ❌ No matching items found.")

        print("-" * 60)

