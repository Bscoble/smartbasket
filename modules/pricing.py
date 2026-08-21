"""
Price scraping and fetching module for SmartBasket.
Handles price lookups from various supermarket websites using APIs and web scraping.
"""

import logging
import re
import time
from datetime import timedelta
from typing import Optional, Dict, Any
from urllib.parse import quote
import requests
from bs4 import BeautifulSoup
from apify_client import ApifyClient
from config import (
    STORES,
    ZENROWS_API_URL,
    ZENROWS_PARAMS,
    APIFY_DEFAULT_CONFIG,
    REQUEST_TIMEOUT,
    APIFY_RUN_TIMEOUT,
    PRICE_REGEX,
    DEFAULT_PRICE_FALLBACK,
    APIFY_DEFAULT_PRICE,
    MIN_VALID_PRICE,
    MAX_VALID_PRICE,
    PRICE_VALIDITY_THRESHOLD,
    BULK_SCRAPE_MAX_RETRIES,
    BULK_SCRAPE_RETRY_DELAY_SECS,
)
from helpers import (
    extract_price_from_text,
    is_valid_price,
    clean_price_text,
    build_store_search_candidates,
)

logger = logging.getLogger(__name__)


class PriceScraper:
    """Handles price scraping from various supermarkets."""
    
    def __init__(self, apify_token: str, zenrows_key: str):
        """
        Initialize the price scraper with API credentials.
        
        Args:
            apify_token: Apify API token for Woolworths/Coles
            zenrows_key: ZenRows API key for Aldi
        """
        self.apify_token = apify_token
        self.zenrows_key = zenrows_key
        # Optional callable(store, query, status, duration_secs, cost_usd, product_count) for
        # usage/cost tracking. Left as None by default so scraping never depends on logging.
        self.usage_logger = None
        logger.info("PriceScraper initialized")

    def _log_apify_usage(self, store: str, query: str, run, product_count: int = 0) -> None:
        """Best-effort logging of an Apify run's cost/duration/status; never raises."""
        if not self.usage_logger or run is None:
            return
        try:
            stats = getattr(run, "stats", None)
            duration_secs = getattr(stats, "run_time_secs", None) if stats else None
            cost_usd = getattr(run, "usage_total_usd", None)
            self.usage_logger(
                store=store,
                query=query,
                status=getattr(run, "status", "unknown"),
                duration_secs=duration_secs,
                cost_usd=cost_usd,
                product_count=product_count,
            )
        except Exception as e:
            logger.debug(f"Usage logging failed for {store}/{query}: {e}")
    
    def get_live_price(self, store: str, item_name: str) -> float:
        """
        Get live price for an item from a specific store.
        
        Args:
            store: Store name (Woolworths, Coles, or Aldi)
            item_name: Item name to search for
            
        Returns:
            Price as float, or DEFAULT_PRICE_FALLBACK if not found
        """
        if store not in STORES:
            logger.warning(f"Unknown store: {store}")
            return DEFAULT_PRICE_FALLBACK
        
        try:
            if store in ["Woolworths", "Coles"]:
                return self._get_apify_price(store, item_name)
            elif store == "Aldi":
                return self._get_zenrows_price(store, item_name)
            else:
                return DEFAULT_PRICE_FALLBACK
        except Exception as e:
            logger.error(f"Error fetching price for {store}/{item_name}: {e}", exc_info=True)
            return DEFAULT_PRICE_FALLBACK

    def get_live_price_result(self, store: str, item_name: str) -> Dict[str, Any]:
        """Return a price together with a reason when it is unavailable."""
        if store not in STORES:
            return {"price": None, "status": "configuration", "message": "Unknown supermarket"}

        try:
            if store in ["Woolworths", "Coles"]:
                return self._get_apify_price_result(store, item_name)
            if store == "Aldi":
                return self._get_zenrows_price_result(store, item_name)
            return {"price": None, "status": "configuration", "message": "Unsupported supermarket"}
        except requests.Timeout:
            return {"price": None, "status": "timeout", "message": "The supermarket request timed out"}
        except requests.RequestException:
            return {"price": None, "status": "connection", "message": "Could not connect to the supermarket service"}
        except Exception as e:
            logger.error(f"Error fetching structured price for {store}/{item_name}: {e}", exc_info=True)
            return {"price": None, "status": "scraper_error", "message": "The supermarket scraper failed"}

    def get_bulk_products(self, store: str, urls, max_items: int = 20) -> "list[Dict[str, Any]]":
        """
        Scrape one or more listing/search-result pages in a single Apify run and
        return every valid product found (accepts a single URL string or a list,
        e.g. several &pageNumber= variants of the same search for pagination).

        Unlike _get_apify_price_result, this has no query to match against, so
        it skips relevance filtering entirely - every product on the page is
        in scope. Returns size-aware names plus standard/special price info,
        since bulk listings (unlike single-item lookups) can contain several
        sizes of the same base product name.

        Retries a few times on a zero-result SUCCEEDED run: retailer anti-bot/
        JS-challenge pages can silently return an empty dataset even though the
        actor reports success, and retrying often recovers real results.
        """
        if not self.apify_token:
            return []

        url_list = [urls] if isinstance(urls, str) else list(urls)
        query_label = "|".join(url_list)

        for attempt in range(1, BULK_SCRAPE_MAX_RETRIES + 2):
            try:
                store_config = STORES[store]
                client = ApifyClient(self.apify_token)
                run_input = {
                    "urls": url_list,
                    **{**APIFY_DEFAULT_CONFIG, "max_items_per_url": max_items},
                }
                run = client.actor(store_config["api_actor"]).call(
                    run_input=run_input,
                    wait_duration=timedelta(seconds=APIFY_RUN_TIMEOUT),
                )
                if run is None or run.status != "SUCCEEDED":
                    self._log_apify_usage(store, query_label, run)
                    return []

                results = []
                for product in self._iter_apify_products(client.dataset(run.default_dataset_id).list_items().items):
                    info = self._extract_bulk_product_info(store, product)
                    if info and is_valid_price(info["price"]) and info["price"] < PRICE_VALIDITY_THRESHOLD:
                        results.append(info)
                self._log_apify_usage(store, query_label, run, len(results))

                if results or attempt > BULK_SCRAPE_MAX_RETRIES:
                    return results

                logger.info(
                    f"Zero results for {store}/{query_label} on attempt {attempt}; retrying"
                )
                time.sleep(BULK_SCRAPE_RETRY_DELAY_SECS)
            except Exception as e:
                logger.error(f"Bulk category scrape failed for {store}/{url_list}: {e}", exc_info=True)
                return []

        return []

    @staticmethod
    def _extract_bulk_product_info(store: str, product: Dict) -> Optional[Dict[str, Any]]:
        """Build a size-aware product record from a category/listing item, per store schema."""
        if store == "Woolworths":
            product_name = (product.get("display_name") or product.get("name") or "").strip()
            price = PriceScraper._coerce_apify_price(product.get("price"))
            standard_price = PriceScraper._coerce_apify_price(product.get("was_price")) or price
            is_special = bool(product.get("is_on_special"))
            brand = None
            unit_price = PriceScraper._coerce_apify_price(product.get("cup_price"))
            unit_label = product.get("cup_measure") or ""
            image_url = product.get("medium_image_file") or product.get("large_image_file") or ""
        elif store == "Coles":
            name = (product.get("name") or "").strip()
            size = (product.get("size") or "").strip()
            product_name = f"{name} {size}".strip() if size else name
            pricing = product.get("pricing") or {}
            price = PriceScraper._coerce_apify_price(pricing.get("now"))
            was = PriceScraper._coerce_apify_price(pricing.get("was"))
            standard_price = was if was else price
            is_special = bool(pricing.get("online_special")) or bool(was)
            brand = product.get("brand")
            unit_info = pricing.get("unit") or {}
            unit_price = PriceScraper._coerce_apify_price(unit_info.get("price"))
            measure_qty = unit_info.get("of_measure_quantity")
            measure_units = unit_info.get("of_measure_units")
            unit_label = f"{measure_qty}{measure_units}" if measure_qty and measure_units else ""
            image_uris = product.get("image_uris") or []
            image_path = image_uris[0].get("uri") if image_uris and isinstance(image_uris[0], dict) else ""
            image_url = f"{STORES['Coles']['image_base_url']}{image_path}" if image_path else ""
        else:
            return None

        if not product_name or price is None:
            return None

        return {
            "product_name": product_name,
            "brand": brand,
            "price": price,
            "standard_price": standard_price,
            "is_special": is_special,
            "unit_price": unit_price,
            "unit_label": unit_label,
            "image_url": image_url,
        }

    def _get_apify_price_result(self, store: str, item_name: str) -> Dict[str, Any]:
        if not self.apify_token:
            return {"price": None, "status": "configuration", "message": "Apify is not configured"}

        try:
            store_config = STORES[store]
            client = ApifyClient(self.apify_token)
            search_candidates = build_store_search_candidates(item_name, store)[:3]
            for search_query in search_candidates:
                search_url = store_config["search_url"].format(quote(search_query))
                run = client.actor(store_config["api_actor"]).call(
                    run_input={"urls": [search_url], **APIFY_DEFAULT_CONFIG},
                    wait_duration=timedelta(seconds=APIFY_RUN_TIMEOUT),
                )
                if run is None or run.status != "SUCCEEDED":
                    self._log_apify_usage(store, search_query, run)
                    return {"price": None, "status": "timeout", "message": "The supermarket request timed out"}
                candidates = []
                for product in self._iter_apify_products(client.dataset(run.default_dataset_id).list_items().items):
                    price = self._extract_apify_price(product)
                    if price and is_valid_price(price) and price < PRICE_VALIDITY_THRESHOLD:
                        relevance = self._product_relevance(item_name, product)
                        if relevance is not None:
                            candidates.append((relevance, price, product))
                if candidates:
                    _, price, product = max(candidates, key=lambda candidate: candidate[0])
                    product_name = self._extract_product_name(product) or item_name
                    self._log_apify_usage(store, search_query, run, len(candidates))
                    return {
                        "price": price,
                        "product_name": product_name,
                        "status": "ok",
                        "message": f"Price found: {product_name}",
                    }
                self._log_apify_usage(store, search_query, run, 0)
            return {"price": None, "status": "not_found", "message": "No matching product price was found"}
        except requests.Timeout:
            return {"price": None, "status": "timeout", "message": "The supermarket request timed out"}
        except requests.RequestException:
            return {"price": None, "status": "connection", "message": "Could not connect to the supermarket service"}
        except Exception as e:
            logger.error(f"Apify error for {store}/{item_name}: {e}", exc_info=True)
            return {"price": None, "status": "scraper_error", "message": "The supermarket scraper failed"}

    def _get_zenrows_price_result(self, store: str, item_name: str) -> Dict[str, Any]:
        if not self.zenrows_key:
            return {"price": None, "status": "configuration", "message": "ZenRows is not configured"}

        try:
            target_url = STORES[store]["search_url"].format(quote(item_name))
            response = requests.get(
                ZENROWS_API_URL,
                params={
                    "apikey": self.zenrows_key,
                    "url": target_url,
                    **ZENROWS_PARAMS,
                    "wait": "5000",
                },
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            selector = ".box--price .value, .product-price, .price, span.price" if store == "Aldi" else ".item-price, .price"
            price_element = soup.select_one(selector)
            price = self._parse_price_from_element(price_element.text, store) if price_element else None
            if not price:
                price = self._extract_price_from_page_text(soup.get_text(), store)
            if price and is_valid_price(price) and price < PRICE_VALIDITY_THRESHOLD:
                product_name = self._extract_zenrows_product_name(soup, item_name)
                return {
                    "price": price,
                    "product_name": product_name,
                    "status": "ok",
                    "message": f"Price found: {product_name}",
                }
            return {"price": None, "status": "not_found", "message": "No matching product price was found"}
        except requests.Timeout:
            return {"price": None, "status": "timeout", "message": "The supermarket request timed out"}
        except requests.RequestException:
            return {"price": None, "status": "connection", "message": "Could not connect to the supermarket service"}
        except Exception as e:
            logger.error(f"ZenRows error for {store}/{item_name}: {e}", exc_info=True)
            return {"price": None, "status": "scraper_error", "message": "The supermarket scraper failed"}
    
    def _get_apify_price(self, store: str, item_name: str) -> float:
        """
        Fetch price from Woolworths or Coles using Apify scrapers.
        
        Args:
            store: "Woolworths" or "Coles"
            item_name: Item name to search
            
        Returns:
            Price as float, or APIFY_DEFAULT_PRICE if not found
        """
        if not self.apify_token:
            logger.warning(f"Apify token not configured, skipping {store}")
            return APIFY_DEFAULT_PRICE
        
        try:
            store_config = STORES.get(store)
            if not store_config:
                return APIFY_DEFAULT_PRICE
            
            client = ApifyClient(self.apify_token)
            actor = store_config.get("api_actor", "")
            search_url = store_config["search_url"].format(quote(item_name))
            
            run_input = {
                "urls": [search_url],
                **APIFY_DEFAULT_CONFIG,
            }
            
            logger.debug(f"Calling Apify actor for {store}: {actor}")
            run = client.actor(actor).call(
                run_input=run_input,
                wait_duration=timedelta(seconds=APIFY_RUN_TIMEOUT),
            )
            if run is None or run.status != "SUCCEEDED":
                logger.debug(f"Apify run for {store}/{item_name} did not finish within {APIFY_RUN_TIMEOUT}s")
                return APIFY_DEFAULT_PRICE
            
            # Parse results from Apify dataset
            for product in self._iter_apify_products(client.dataset(run.default_dataset_id).list_items().items):
                price = self._extract_apify_price(product)
                if price and is_valid_price(price):
                    logger.info(f"Found price for {item_name} at {store}: {price}")
                    return price
            
            logger.debug(f"No valid price found via Apify for {item_name} at {store}")
            return APIFY_DEFAULT_PRICE
        except Exception as e:
            logger.error(f"Apify error for {store}/{item_name}: {e}", exc_info=True)
            return APIFY_DEFAULT_PRICE
    
    @staticmethod
    def _iter_apify_products(items) -> "list[Dict]":
        """
        Flatten Apify dataset items into individual product records.

        The Woolworths/Coles search actors return one dataset item per
        searched URL, with the actual matched products nested under a
        "products" list rather than as flat top-level records.
        """
        products = []
        for item in items or []:
            if not isinstance(item, dict):
                continue

            for key in ("products", "results", "items", "data"):
                nested_products = item.get(key)
                if isinstance(nested_products, list):
                    products.extend(p for p in nested_products if isinstance(p, dict))
                    break
            else:
                products.append(item)
        return products

    @staticmethod
    def _extract_product_name(product: Dict) -> Optional[str]:
        """Return the most specific product name exposed by a retailer actor."""
        for field in ("name", "display_name", "product_name", "title"):
            value = product.get(field)
            if value:
                return str(value).strip()
        return None

    @classmethod
    def _product_relevance(cls, query: str, product: Dict) -> Optional[float]:
        """Score a retailer product and reject results missing important query terms."""
        product_name = cls._extract_product_name(product)
        if not product_name:
            return None

        def normalize(value: str) -> list[str]:
            normalized = re.sub(r"[^a-z0-9]+", " ", value.lower().replace("'", ""))
            terms = normalized.split()
            aliases = {"barbecue": "bbq", "tams": "tam"}
            return [aliases.get(term, term) for term in terms]
        query_terms = normalize(query)
        product_terms = set(normalize(product_name))
        if not query_terms:
            return None

        ignored_terms = {"the", "and", "of", "a", "an"}
        meaningful_terms = [term for term in query_terms if term not in ignored_terms]
        missing_terms = [term for term in meaningful_terms if term not in product_terms]

        # A missing pack size or explicit product descriptor is a wrong match.
        size_terms = [term for term in meaningful_terms if any(char.isdigit() for char in term)]
        descriptor_terms = {
            "full", "cream", "light", "bbq", "barbecue", "shapes", "tim", "tam",
        }
        missing_required = [
            term for term in missing_terms
            if term in size_terms or term in descriptor_terms
        ]
        if missing_required:
            return None

        matched = len(meaningful_terms) - len(missing_terms)
        return matched / len(meaningful_terms)

    @staticmethod
    def _coerce_apify_price(value: Any) -> Optional[float]:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            match = re.search(r"\d+(?:\.\d+)?", value.replace(",", ""))
            return float(match.group()) if match else None
        if isinstance(value, dict):
            for key in ("amount", "value", "now", "price", "unitPrice", "instore_price"):
                if key in value:
                    parsed = PriceScraper._coerce_apify_price(value[key])
                    if parsed is not None:
                        return parsed
        if isinstance(value, list):
            for nested in value:
                parsed = PriceScraper._coerce_apify_price(nested)
                if parsed is not None:
                    return parsed
        return None

    def _extract_apify_price(self, item: Dict) -> Optional[float]:
        """
        Extract price from an Apify product record.
        
        Args:
            item: Product dictionary from Apify response
            
        Returns:
            Price as float or None
        """
        try:
            for field in ("pricing", "price", "instore_price", "unitPrice", "amount", "value", "now", "offers"):
                if field in item:
                    price = self._coerce_apify_price(item[field])
                    if price is not None and is_valid_price(price):
                        return price
        except (ValueError, TypeError, KeyError) as e:
            logger.debug(f"Failed to extract price from Apify item: {e}")
        
        return None
    
    def _get_zenrows_price(self, store: str, item_name: str) -> float:
        """
        Fetch price from Aldi using ZenRows web scraping.
        
        Args:
            store: "Aldi"
            item_name: Item name to search
            
        Returns:
            Price as float, or DEFAULT_PRICE_FALLBACK if not found
        """
        if not self.zenrows_key:
            logger.warning(f"ZenRows key not configured, skipping {store}")
            return DEFAULT_PRICE_FALLBACK
        
        try:
            store_config = STORES.get(store)
            if not store_config:
                return DEFAULT_PRICE_FALLBACK
            
            target_url = store_config["search_url"].format(quote(item_name))
            
            params = {
                "apikey": self.zenrows_key,
                "url": target_url,
                **ZENROWS_PARAMS,
            }
            
            logger.debug(f"Fetching from {store} via ZenRows: {target_url}")
            response = requests.get(
                ZENROWS_API_URL,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, "html.parser")
            
            # Log first 800 chars for debugging Aldi pages
            if store == "Aldi":
                logger.debug(f"Aldi page text (first 800 chars): {soup.text[:800]}")
            
            price_element = soup.select_one(".box--price .value, .product-price, .price, span.price")
            
            # Extract price from element
            if price_element:
                return self._parse_price_from_element(price_element.text, store)
            
            # Fallback: search entire page text for price patterns
            price = self._extract_price_from_page_text(soup.get_text(), store)
            if price and is_valid_price(price):
                logger.info(f"Found price for {item_name} at {store}: {price}")
                return price
            
            logger.debug(f"No valid price found via ZenRows for {item_name} at {store}")
            return DEFAULT_PRICE_FALLBACK
        except requests.RequestException as e:
            logger.error(f"ZenRows request error for {store}/{item_name}: {e}")
            return DEFAULT_PRICE_FALLBACK
        except Exception as e:
            logger.error(f"ZenRows parsing error for {store}/{item_name}: {e}", exc_info=True)
            return DEFAULT_PRICE_FALLBACK
    
    def _parse_price_from_element(self, text: str, store: str) -> Optional[float]:
        """
        Parse price from a price element's text.
        
        Args:
            text: Text content of price element
            store: Store name (for logging)
            
        Returns:
            Price as float or None
        """
        try:
            clean_text = clean_price_text(text)
            
            # Try to parse as simple float first
            try:
                return float(clean_text)
            except ValueError:
                pass
            
            # Try regex extraction
            price = extract_price_from_text(clean_text)
            return price
        except Exception as e:
            logger.debug(f"Error parsing price for {store}: {e}")
            return None

    @staticmethod
    def _extract_zenrows_product_name(soup: BeautifulSoup, fallback: str) -> str:
        """Extract a visible product title when the retailer page exposes one."""
        selectors = [
            '[data-product-card="true"] h2',
            '[data-product-card="true"] h3',
            '[data-product-card="true"] [class*="name"]',
            '[data-product-card="true"] [class*="title"]',
            'a[href*="/product/"] h2',
            'a[href*="/product/"] h3',
        ]
        for selector in selectors:
            element = soup.select_one(selector)
            if element:
                title = " ".join(element.get_text(" ", strip=True).split())
                if title:
                    return title
        return fallback
    
    def _extract_price_from_page_text(self, page_text: str, store: str) -> Optional[float]:
        """
        Extract price from page text using regex patterns.
        Returns the first valid price found within reasonable bounds.
        
        Args:
            page_text: Full page text to search
            store: Store name (for logging)
            
        Returns:
            Price as float or None
        """
        try:
            # Find all price patterns like $12.50
            prices_found = re.findall(PRICE_REGEX, page_text)
            
            if prices_found:
                # Filter to valid prices and return first one
                for price_str in prices_found:
                    try:
                        price = float(price_str)
                        if is_valid_price(price, MIN_VALID_PRICE, MAX_VALID_PRICE):
                            logger.info(
                                f"Found price for {store} via page text extraction: {price}"
                            )
                            return price
                    except ValueError:
                        continue
            
            logger.debug(f"No valid prices found in page text for {store}")
            return None
        except Exception as e:
            logger.error(f"Error extracting price from page text for {store}: {e}")
            return None
