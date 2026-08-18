"""
Barcode and product database module for SmartBasket.
Handles barcode scanning, decoding, and product lookup from Open Food Facts.
"""

import logging
from typing import Optional, Tuple, List, Dict
from PIL import Image, ImageEnhance, ImageOps
from pyzbar.pyzbar import decode
import requests
from config import (
    OPEN_FOOD_FACTS_BARCODE_URL,
    OPEN_FOOD_FACTS_SEARCH_URL,
    OPEN_FOOD_FACTS_USER_AGENT,
    REQUEST_TIMEOUT,
    BARCODE_BRIGHTNESS_THRESHOLD,
)

logger = logging.getLogger(__name__)


class BarcodeScanner:
    """Handles barcode scanning and detection with multiple enhancement passes."""
    
    @staticmethod
    def decode_barcode(image_file) -> Optional[str]:
        """
        Decode barcode from an image file using multi-pass enhancement.
        Tries multiple image processing techniques to improve detection.
        
        Args:
            image_file: Image file object or path
            
        Returns:
            Barcode string if found, None otherwise
        """
        try:
            img = Image.open(image_file)
            return BarcodeScanner._try_all_decode_passes(img)
        except Exception as e:
            logger.error(f"Error opening image file: {e}", exc_info=True)
            return None
    
    @staticmethod
    def _try_all_decode_passes(img: Image.Image) -> Optional[str]:
        """
        Try multiple image processing passes to decode barcode.
        
        Args:
            img: PIL Image object
            
        Returns:
            Barcode string if found, None otherwise
        """
        # Pass 1: Raw image
        result = BarcodeScanner._check_image(img)
        if result:
            logger.info(f"Barcode decoded on pass 1 (raw): {result}")
            return result
        
        # Pass 2: Grayscale & High Contrast
        gray = ImageOps.grayscale(img)
        enhancer = ImageEnhance.Contrast(gray)
        high_contrast = enhancer.enhance(2.0)
        
        result = BarcodeScanner._check_image(high_contrast)
        if result:
            logger.info(f"Barcode decoded on pass 2 (high contrast): {result}")
            return result
        
        # Pass 3: Sharpened High Contrast
        sharpener = ImageEnhance.Sharpness(high_contrast)
        sharp = sharpener.enhance(2.5)
        
        result = BarcodeScanner._check_image(sharp)
        if result:
            logger.info(f"Barcode decoded on pass 3 (sharpened): {result}")
            return result
        
        # Pass 4: Auto-rotations (90, 180, 270 degrees)
        for angle in [90, 180, 270]:
            rotated = high_contrast.rotate(angle, expand=True)
            result = BarcodeScanner._check_image(rotated)
            if result:
                logger.info(f"Barcode decoded on pass 4 (rotated {angle}°): {result}")
                return result
        
        # Pass 5: Binarization
        bw = gray.point(lambda p: 255 if p > BARCODE_BRIGHTNESS_THRESHOLD else 0)
        result = BarcodeScanner._check_image(bw)
        if result:
            logger.info(f"Barcode decoded on pass 5 (binarized): {result}")
            return result
        
        logger.warning("Barcode decoding failed: no barcode detected in image")
        return None
    
    @staticmethod
    def _check_image(img: Image.Image) -> Optional[str]:
        """
        Check if an image contains a decodable barcode.
        
        Args:
            img: PIL Image object
            
        Returns:
            Decoded barcode string or None
        """
        try:
            decoded_objects = decode(img)
            for obj in decoded_objects:
                barcode_data = obj.data.decode("utf-8")
                logger.debug(f"Detected barcode type {obj.type}: {barcode_data}")
                return barcode_data
        except Exception as e:
            logger.debug(f"Error checking image for barcode: {e}")
        
        return None


class ProductLookup:
    """Handles product lookup from Open Food Facts database."""
    
    @staticmethod
    def lookup_barcode_product(barcode: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Look up product name and image from Open Food Facts by barcode.
        
        Args:
            barcode: Barcode/EAN number
            
        Returns:
            Tuple of (product_name, image_url) or (None, None) if not found
        """
        try:
            url = OPEN_FOOD_FACTS_BARCODE_URL.format(barcode)
            headers = {"User-Agent": OPEN_FOOD_FACTS_USER_AGENT}
            
            logger.debug(f"Looking up barcode {barcode} in Open Food Facts")
            response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            
            data = response.json()
            
            # Check if product was found
            if data.get("status") != 1:
                logger.warning(f"Barcode {barcode} not found in Open Food Facts")
                return None, None
            
            product = data.get("product", {})
            name = product.get("product_name_en") or product.get("product_name")
            brand = product.get("brands")
            image_url = product.get("image_front_url") or product.get("image_url") or ""
            
            title = f"{brand} {name}".strip() if brand else name
            
            if title:
                logger.info(f"Found product for barcode {barcode}: {title}")
                return title, image_url
            else:
                logger.warning(f"Product found but no name for barcode {barcode}")
                return None, None
        except requests.RequestException as e:
            logger.error(f"Request error looking up barcode {barcode}: {e}")
            return None, None
        except Exception as e:
            logger.error(f"Error looking up barcode {barcode}: {e}", exc_info=True)
            return None, None
    
    @staticmethod
    def search_product_by_name(query: str) -> List[Dict[str, str]]:
        """
        Search Open Food Facts database by product name.
        
        Args:
            query: Product name search query
            
        Returns:
            List of matching products with title and image_url
        """
        try:
            logger.debug(f"Searching Open Food Facts for: {query}")
            
            params = {
                "search_terms": query,
                "search_simple": 1,
                "action": "process",
                "json": 1,
                "page_size": 5,
            }
            
            headers = {"User-Agent": OPEN_FOOD_FACTS_USER_AGENT}
            
            response = requests.get(
                OPEN_FOOD_FACTS_SEARCH_URL,
                params=params,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            
            data = response.json()
            results = []
            seen_titles = set()
            
            for product in data.get("products", []):
                name = product.get("product_name_en") or product.get("product_name")
                brand = product.get("brands")
                image_url = product.get("image_front_url") or product.get("image_url") or ""
                
                title = f"{brand} {name}".strip() if brand else name
                
                # Deduplicate results
                if title and title not in seen_titles:
                    results.append({"title": title, "image_url": image_url})
                    seen_titles.add(title)
            
            logger.info(f"Found {len(results)} products for query: {query}")
            return results
        except requests.RequestException as e:
            logger.error(f"Request error searching for products: {e}")
            return []
        except Exception as e:
            logger.error(f"Error searching for products: {e}", exc_info=True)
            return []
