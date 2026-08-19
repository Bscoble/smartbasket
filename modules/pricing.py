"""
Price scraping and fetching module for SmartBasket.
Handles price lookups from various supermarket websites using APIs and web scraping.
"""

import logging
import re
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
)
from helpers import extract_price_from_text, is_valid_price, clean_price_text

logger = logging.getLogger(__name__)


class PriceScraper:
    """Handles price scraping from various supermarkets."""
    
    def __init__(self, apify_token: str, zenrows_key: str):
        """
        Initialize the price scraper with API credentials.
        
        Args:
            apify_token: Apify API token for Woolworths/Coles
            zenrows_key: ZenRows API key for Aldi/IGA
        """
        self.apify_token = apify_token
        self.zenrows_key = zenrows_key
        logger.info("PriceScraper initialized")
    
    def get_live_price(self, store: str, item_name: str) -> float:
        """
        Get live price for an item from a specific store.
        
        Args:
            store: Store name (Woolworths, Coles, Aldi, IGA)
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
            elif store in ["Aldi", "IGA"]:
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
            if store in ["Aldi", "IGA"]:
                return self._get_zenrows_price_result(store, item_name)
            return {"price": None, "status": "configuration", "message": "Unsupported supermarket"}
        except requests.Timeout:
            return {"price": None, "status": "timeout", "message": "The supermarket request timed out"}
        except requests.RequestException:
            return {"price": None, "status": "connection", "message": "Could not connect to the supermarket service"}
        except Exception as e:
            logger.error(f"Error fetching structured price for {store}/{item_name}: {e}", exc_info=True)
            return {"price": None, "status": "scraper_error", "message": "The supermarket scraper failed"}

    def _get_apify_price_result(self, store: str, item_name: str) -> Dict[str, Any]:
        if not self.apify_token:
            return {"price": None, "status": "configuration", "message": "Apify is not configured"}

        try:
            store_config = STORES[store]
            client = ApifyClient(self.apify_token)
            search_url = store_config["search_url"].format(quote(item_name))
            run = client.actor(store_config["api_actor"]).call(
                run_input={"urls": [search_url], **APIFY_DEFAULT_CONFIG},
                wait_duration=timedelta(seconds=APIFY_RUN_TIMEOUT),
            )
            if run is None or run.status != "SUCCEEDED":
                return {"price": None, "status": "timeout", "message": "The supermarket request timed out"}
            for item in client.dataset(run.default_dataset_id).list_items().items:
                price = self._extract_apify_price(item)
                if price and is_valid_price(price):
                    return {"price": price, "status": "ok", "message": "Price found"}
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
                params={"apikey": self.zenrows_key, "url": target_url, **ZENROWS_PARAMS},
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            selector = ".box--price .value, .product-price, .price, span.price" if store == "Aldi" else ".item-price, .price"
            price_element = soup.select_one(selector)
            price = self._parse_price_from_element(price_element.text, store) if price_element else None
            if not price:
                price = self._extract_price_from_page_text(soup.get_text(), store)
            if price and is_valid_price(price):
                return {"price": price, "status": "ok", "message": "Price found"}
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
            for item in client.dataset(run.default_dataset_id).list_items().items:
                price = self._extract_apify_price(item)
                if price and is_valid_price(price):
                    logger.info(f"Found price for {item_name} at {store}: {price}")
                    return price
            
            logger.debug(f"No valid price found via Apify for {item_name} at {store}")
            return APIFY_DEFAULT_PRICE
        except Exception as e:
            logger.error(f"Apify error for {store}/{item_name}: {e}", exc_info=True)
            return APIFY_DEFAULT_PRICE
    
    def _extract_apify_price(self, item: Dict) -> Optional[float]:
        """
        Extract price from Apify result item.
        
        Args:
            item: Item dictionary from Apify response
            
        Returns:
            Price as float or None
        """
        try:
            # Try different price field locations in Apify response
            if "pricing" in item and "now" in item["pricing"]:
                return float(item["pricing"]["now"])
            elif "price" in item:
                price_str = str(item["price"]).replace("$", "")
                return float(price_str)
        except (ValueError, TypeError, KeyError) as e:
            logger.debug(f"Failed to extract price from Apify item: {e}")
        
        return None
    
    def _get_zenrows_price(self, store: str, item_name: str) -> float:
        """
        Fetch price from Aldi or IGA using ZenRows web scraping.
        
        Args:
            store: "Aldi" or "IGA"
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
            
            # Try store-specific selectors
            if store == "Aldi":
                price_element = soup.select_one(".box--price .value, .product-price, .price, span.price")
            else:  # IGA
                price_element = soup.select_one(".item-price, .price")
            
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
