"""
Google Sheets integration module for SmartBasket.
Handles all interactions with Google Sheets for data persistence.
"""

import logging
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timedelta
import gspread
from config import (
    WORKSHEET_NAMES,
    WORKSHEET_CONFIG,
    DATETIME_FORMAT,
    DATETIME_TIME_FORMAT,
    CACHE_EXPIRY_HOURS_VALID,
    CACHE_EXPIRY_HOURS_INVALID,
    PRICE_VALIDITY_THRESHOLD,
    SHEETS_READ_CACHE_SECONDS,
    STANDARD_PRICE_MAX_AGE_DAYS,
)

logger = logging.getLogger(__name__)


class SheetsManager:
    """Manages all Google Sheets operations for SmartBasket."""
    
    def __init__(self, spreadsheet):
        """
        Initialize the SheetsManager with a gspread spreadsheet object.
        
        Args:
            spreadsheet: Authorized gspread spreadsheet object
        """
        self.sh = spreadsheet
        self._worksheet_cache: Dict[str, Any] = {}
        self._values_cache: Dict[str, Tuple[datetime, List[List[str]]]] = {}
        logger.info("SheetsManager initialized")

    def _cached_values(self, worksheet_name: str, ws: Any, force_refresh: bool = False) -> List[List[str]]:
        """Return worksheet values using a short-lived in-memory cache."""
        if not force_refresh:
            cached = self._values_cache.get(worksheet_name)
            if cached:
                cached_at, data = cached
                if (datetime.now() - cached_at).total_seconds() < SHEETS_READ_CACHE_SECONDS:
                    return data

        data = ws.get_all_values()
        self._values_cache[worksheet_name] = (datetime.now(), data)
        return data

    def _invalidate_values_cache(self, worksheet_name: str) -> None:
        """Invalidate cached values after writes to a worksheet."""
        self._values_cache.pop(worksheet_name, None)
    
    def _get_or_create_worksheet(self, name: str, rows: str = "1000", cols: str = "4") -> Any:
        """
        Get an existing worksheet or create it if it doesn't exist.
        
        Args:
            name: Worksheet name
            rows: Number of rows (if creating)
            cols: Number of columns (if creating)
            
        Returns:
            Worksheet object
        """
        cached_ws = self._worksheet_cache.get(name)
        if cached_ws is not None:
            return cached_ws

        try:
            ws = self.sh.worksheet(name)
        except gspread.WorksheetNotFound:
            logger.info(f"Creating new worksheet: {name}")
            ws = self.sh.add_worksheet(title=name, rows=rows, cols=cols)

        self._worksheet_cache[name] = ws
        return ws
    
    # ========================================================================
    # STORE PREFERENCES
    # ========================================================================
    
    def load_store_preferences(self) -> Dict[str, bool]:
        """
        Load store preferences from the Preferences worksheet.
        
        Returns:
            Dictionary of store preferences {store_name: enabled}
        """
        default_prefs = {"Woolworths": True, "Coles": True, "Aldi": True}
        
        try:
            worksheet_name = WORKSHEET_NAMES["preferences"]
            pref_ws = self._get_or_create_worksheet(worksheet_name)
            data = self._cached_values(worksheet_name, pref_ws)
            
            if len(data) >= 2:
                return {
                    "Woolworths": data[1][0] == "True",
                    "Coles": data[1][1] == "True",
                    "Aldi": data[1][2] == "True",
                }
        except Exception as e:
            logger.error(f"Error loading store preferences: {e}", exc_info=True)
        
        return default_prefs
    
    def save_store_preferences(self, prefs: Dict[str, bool]) -> bool:
        """
        Save store preferences to the Preferences worksheet.
        
        Args:
            prefs: Dictionary of store preferences
            
        Returns:
            True if successful, False otherwise
        """
        try:
            worksheet_name = WORKSHEET_NAMES["preferences"]
            pref_ws = self._get_or_create_worksheet(worksheet_name)
            pref_ws.update(
                "A1:C2",
                [
                    ["Woolworths", "Coles", "Aldi"],
                    [
                        str(prefs.get("Woolworths", True)),
                        str(prefs.get("Coles", True)),
                        str(prefs.get("Aldi", True)),
                    ],
                ],
            )
            self._invalidate_values_cache(worksheet_name)
            logger.info("Store preferences saved successfully")
            return True
        except Exception as e:
            logger.error(f"Error saving store preferences: {e}", exc_info=True)
            return False
    
    # ========================================================================
    # PRICE CACHE
    # ========================================================================
    
    def load_price_cache(self) -> Dict[Tuple[str, str], Dict[str, Any]]:
        """
        Load the entire price cache from sheets into memory.
        
        Returns:
            Dictionary with cache key (store, item_lower) and cached data
        """
        cache = {}
        
        try:
            worksheet_name = WORKSHEET_NAMES["price_cache"]
            ws = self._get_or_create_worksheet(
                worksheet_name,
                rows=WORKSHEET_CONFIG["price_cache"]["rows"],
                cols=WORKSHEET_CONFIG["price_cache"]["cols"],
            )
            
            # Add header if new worksheet
            if ws.row_count == 1:
                ws.append_row(["Store", "Item", "Price", "Timestamp", "Product Name"])
                return cache
            
            data = self._cached_values(worksheet_name, ws)
            
            if len(data) > 1:
                for row in data[1:]:
                    if len(row) >= 4:
                        store, item, price_str, ts_str = row[0], row[1], row[2], row[3]
                        try:
                            ts = datetime.strptime(ts_str, DATETIME_TIME_FORMAT)
                            price = float(price_str)
                            cache[(store, item.lower())] = {
                                "price": price,
                                "timestamp": ts,
                                "product_name": row[4] if len(row) >= 5 else "",
                            }
                        except (ValueError, IndexError) as e:
                            logger.debug(f"Skipping invalid cache row: {row}, error: {e}")
                            pass
            
            logger.info(f"Loaded {len(cache)} price cache entries")
        except Exception as e:
            logger.error(f"Error loading price cache: {e}", exc_info=True)
        
        return cache
    
    def save_price_cache(self, cache: Dict[Tuple[str, str], Dict[str, Any]]) -> bool:
        """
        Bulk save the updated cache back to Google Sheets.
        
        Args:
            cache: Cache dictionary to save
            
        Returns:
            True if successful, False otherwise
        """
        try:
            worksheet_name = WORKSHEET_NAMES["price_cache"]
            ws = self._get_or_create_worksheet(
                worksheet_name,
                rows=WORKSHEET_CONFIG["price_cache"]["rows"],
                cols=WORKSHEET_CONFIG["price_cache"]["cols"],
            )
            
            rows = [["Store", "Item", "Price", "Timestamp", "Product Name"]]
            for (store, item), data in cache.items():
                rows.append(
                    [
                        store,
                        item,
                        str(data["price"]),
                        data["timestamp"].strftime(DATETIME_TIME_FORMAT),
                        data.get("product_name", ""),
                    ]
                )
            
            ws.clear()
            ws.append_rows(rows)
            self._invalidate_values_cache(worksheet_name)
            logger.info(f"Saved {len(cache)} price cache entries")
            return True
        except Exception as e:
            logger.error(f"Error saving price cache: {e}", exc_info=True)
            return False
    
    def is_cache_valid(self, cached_data: Dict[str, Any]) -> bool:
        """
        Check if a cached price entry is still valid.
        
        Args:
            cached_data: Cache entry with 'price' and 'timestamp'
            
        Returns:
            True if cache is still valid
        """
        price = cached_data.get("price", 0)
        timestamp = cached_data.get("timestamp")
        
        if not timestamp:
            return False
        
        # Different expiry times based on price validity
        expiry_hours = (
            CACHE_EXPIRY_HOURS_VALID
            if price < PRICE_VALIDITY_THRESHOLD
            else CACHE_EXPIRY_HOURS_INVALID
        )
        
        return (datetime.now() - timestamp) < timedelta(hours=expiry_hours)
    
    # ========================================================================
    # STANDARD PRICES (long-lived shelf prices, refreshed infrequently)
    # ========================================================================

    def load_standard_prices(self) -> Dict[Tuple[str, str], Dict[str, Any]]:
        """Load the standard (non-special) shelf price reference table."""
        prices = {}
        try:
            worksheet_name = WORKSHEET_NAMES["standard_prices"]
            ws = self._get_or_create_worksheet(
                worksheet_name,
                rows=WORKSHEET_CONFIG["standard_prices"]["rows"],
                cols=WORKSHEET_CONFIG["standard_prices"]["cols"],
            )

            if ws.row_count == 1:
                ws.append_row(["Store", "Item", "Price", "Product Name", "Last Verified", "Unit Price", "Unit Label", "Image URL"])
                return prices

            data = self._cached_values(worksheet_name, ws)
            for row in data[1:]:
                if len(row) >= 5:
                    store, item, price_str, product_name, verified_str = row[0], row[1], row[2], row[3], row[4]
                    try:
                        unit_price = float(row[5]) if len(row) >= 6 and row[5] else None
                        unit_label = row[6] if len(row) >= 7 else ""
                        image_url = row[7] if len(row) >= 8 else ""
                        prices[(store, item.lower())] = {
                            "price": float(price_str),
                            "product_name": product_name,
                            "last_verified": datetime.strptime(verified_str, DATETIME_TIME_FORMAT),
                            "unit_price": unit_price,
                            "unit_label": unit_label,
                            "image_url": image_url,
                        }
                    except (ValueError, IndexError) as e:
                        logger.debug(f"Skipping invalid standard price row: {row}, error: {e}")

            logger.info(f"Loaded {len(prices)} standard price entries")
        except Exception as e:
            logger.error(f"Error loading standard prices: {e}", exc_info=True)
        return prices

    def is_standard_price_valid(self, entry: Dict[str, Any]) -> bool:
        """Check whether a standard price entry was verified recently enough to trust."""
        last_verified = entry.get("last_verified")
        if not last_verified:
            return False
        return (datetime.now() - last_verified) < timedelta(days=STANDARD_PRICE_MAX_AGE_DAYS)

    def upsert_standard_price(
        self,
        store: str,
        item_name: str,
        price: float,
        product_name: str = "",
        unit_price: Optional[float] = None,
        unit_label: str = "",
        image_url: str = "",
    ) -> bool:
        """Add or update a single standard price entry, e.g. from an admin/population tool."""
        try:
            worksheet_name = WORKSHEET_NAMES["standard_prices"]
            ws = self._get_or_create_worksheet(
                worksheet_name,
                rows=WORKSHEET_CONFIG["standard_prices"]["rows"],
                cols=WORKSHEET_CONFIG["standard_prices"]["cols"],
            )
            data = self._cached_values(worksheet_name, ws)
            headers = ["Store", "Item", "Price", "Product Name", "Last Verified", "Unit Price", "Unit Label", "Image URL"]
            if not data:
                ws.append_row(headers)
                data = [headers]

            item_lower = item_name.strip().lower()
            values = [
                store,
                item_name.strip(),
                str(price),
                product_name or item_name,
                datetime.now().strftime(DATETIME_TIME_FORMAT),
                str(unit_price) if unit_price is not None else "",
                unit_label,
                image_url,
            ]

            existing_row = None
            for row_number, row in enumerate(data[1:], start=2):
                if len(row) >= 2 and row[0] == store and row[1].strip().lower() == item_lower:
                    existing_row = row_number
                    break

            if existing_row:
                ws.update(f"A{existing_row}:H{existing_row}", [values])
            else:
                ws.append_row(values)
            self._invalidate_values_cache(worksheet_name)
            return True
        except Exception as e:
            logger.error(f"Error upserting standard price: {e}", exc_info=True)
            return False

    def save_standard_prices(self, prices: Dict[Tuple[str, str], Dict[str, Any]]) -> bool:
        """Bulk-overwrite the standard price table from an in-memory dict (avoids one write per item)."""
        try:
            worksheet_name = WORKSHEET_NAMES["standard_prices"]
            ws = self._get_or_create_worksheet(
                worksheet_name,
                rows=WORKSHEET_CONFIG["standard_prices"]["rows"],
                cols=WORKSHEET_CONFIG["standard_prices"]["cols"],
            )

            rows = [["Store", "Item", "Price", "Product Name", "Last Verified", "Unit Price", "Unit Label", "Image URL"]]
            for (store, item), data in prices.items():
                unit_price = data.get("unit_price")
                rows.append(
                    [
                        store,
                        item,
                        str(data["price"]),
                        data.get("product_name", ""),
                        data["last_verified"].strftime(DATETIME_TIME_FORMAT),
                        str(unit_price) if unit_price is not None else "",
                        data.get("unit_label", ""),
                        data.get("image_url", ""),
                    ]
                )

            ws.clear()
            ws.append_rows(rows)
            self._invalidate_values_cache(worksheet_name)
            logger.info(f"Saved {len(prices)} standard price entries")
            return True
        except Exception as e:
            logger.error(f"Error saving standard prices: {e}", exc_info=True)
            return False

    # ========================================================================
    # DAILY SPECIALS (short-lived, refreshed once per day)
    # ========================================================================

    def load_daily_specials(self) -> Dict[Tuple[str, str], Dict[str, Any]]:
        """Load today's active special prices."""
        specials = {}
        try:
            worksheet_name = WORKSHEET_NAMES["daily_specials"]
            ws = self._get_or_create_worksheet(
                worksheet_name,
                rows=WORKSHEET_CONFIG["daily_specials"]["rows"],
                cols=WORKSHEET_CONFIG["daily_specials"]["cols"],
            )

            if ws.row_count == 1:
                ws.append_row(["Store", "Item", "Price", "Product Name", "Date"])
                return specials

            today = datetime.now().strftime("%Y-%m-%d")
            data = self._cached_values(worksheet_name, ws)
            for row in data[1:]:
                if len(row) >= 5 and row[4].strip() == today:
                    store, item, price_str, product_name = row[0], row[1], row[2], row[3]
                    try:
                        specials[(store, item.lower())] = {
                            "price": float(price_str),
                            "product_name": product_name,
                        }
                    except (ValueError, IndexError) as e:
                        logger.debug(f"Skipping invalid special row: {row}, error: {e}")

            logger.info(f"Loaded {len(specials)} active special entries")
        except Exception as e:
            logger.error(f"Error loading daily specials: {e}", exc_info=True)
        return specials

    def save_daily_specials(self, specials: Dict[Tuple[str, str], Dict[str, Any]]) -> bool:
        """Bulk-overwrite today's specials table, e.g. from an overnight scrape job."""
        try:
            worksheet_name = WORKSHEET_NAMES["daily_specials"]
            ws = self._get_or_create_worksheet(
                worksheet_name,
                rows=WORKSHEET_CONFIG["daily_specials"]["rows"],
                cols=WORKSHEET_CONFIG["daily_specials"]["cols"],
            )

            today = datetime.now().strftime("%Y-%m-%d")
            rows = [["Store", "Item", "Price", "Product Name", "Date"]]
            for (store, item), data in specials.items():
                rows.append([store, item, str(data["price"]), data.get("product_name", ""), today])

            ws.clear()
            ws.append_rows(rows)
            self._invalidate_values_cache(worksheet_name)
            logger.info(f"Saved {len(specials)} special price entries")
            return True
        except Exception as e:
            logger.error(f"Error saving daily specials: {e}", exc_info=True)
            return False

    # ========================================================================
    # CRAWL STATE (resumable pagination cursor for bulk category crawls)
    # ========================================================================

    def load_crawl_state(self) -> Dict[Tuple[str, str], Dict[str, Any]]:
        """Load the last page scraped per (store, category) so crawls can resume."""
        state = {}
        try:
            worksheet_name = WORKSHEET_NAMES["crawl_state"]
            ws = self._get_or_create_worksheet(
                worksheet_name,
                rows=WORKSHEET_CONFIG["crawl_state"]["rows"],
                cols=WORKSHEET_CONFIG["crawl_state"]["cols"],
            )

            if ws.row_count == 1:
                ws.append_row(["Store", "Category", "Last Page Scraped", "Last Run"])
                return state

            data = self._cached_values(worksheet_name, ws)
            for row in data[1:]:
                if len(row) >= 3:
                    store, category, last_page_str = row[0], row[1], row[2]
                    try:
                        state[(store, category)] = {
                            "last_page": int(last_page_str),
                            "last_run": row[3] if len(row) >= 4 else "",
                        }
                    except (ValueError, IndexError) as e:
                        logger.debug(f"Skipping invalid crawl state row: {row}, error: {e}")

            logger.info(f"Loaded {len(state)} crawl state entries")
        except Exception as e:
            logger.error(f"Error loading crawl state: {e}", exc_info=True)
        return state

    def save_crawl_state(self, state: Dict[Tuple[str, str], Dict[str, Any]]) -> bool:
        """Bulk-overwrite the crawl state table with updated pagination cursors."""
        try:
            worksheet_name = WORKSHEET_NAMES["crawl_state"]
            ws = self._get_or_create_worksheet(
                worksheet_name,
                rows=WORKSHEET_CONFIG["crawl_state"]["rows"],
                cols=WORKSHEET_CONFIG["crawl_state"]["cols"],
            )

            rows = [["Store", "Category", "Last Page Scraped", "Last Run"]]
            for (store, category), data in state.items():
                rows.append([store, category, str(data["last_page"]), data.get("last_run", "")])

            ws.clear()
            ws.append_rows(rows)
            self._invalidate_values_cache(worksheet_name)
            logger.info(f"Saved {len(state)} crawl state entries")
            return True
        except Exception as e:
            logger.error(f"Error saving crawl state: {e}", exc_info=True)
            return False
    
    # ========================================================================
    # SHOPPING LIST
    # ========================================================================
    
    def get_shopping_list(self, user_id: str) -> List[List[str]]:
        """
        Get all items from the Shopping List worksheet.
        
        Returns:
            List of rows belonging to the authenticated customer
        """
        try:
            worksheet_name = WORKSHEET_NAMES["shopping_list"]
            ws = self._get_or_create_worksheet(worksheet_name)
            normalized_user_id = user_id.strip().lower()
            return [
                row[:4]
                for row in self._cached_values(worksheet_name, ws)
                if len(row) >= 5 and row[4].strip().lower() == normalized_user_id
            ]
        except Exception as e:
            logger.error(f"Error getting shopping list: {e}", exc_info=True)
            return []
    
    def add_item_to_list(
        self,
        item_name: str,
        qty: int,
        unit: str,
        image_url: str = "",
        user_id: str = "",
    ) -> bool:
        """
        Add an item to the shopping list.
        
        Args:
            item_name: Name of the item
            qty: Quantity
            unit: Unit of measurement
            image_url: Optional product image URL
            user_id: Stable identifier for the authenticated customer
            
        Returns:
            True if successful, False otherwise
        """
        try:
            worksheet_name = WORKSHEET_NAMES["shopping_list"]
            ws = self._get_or_create_worksheet(worksheet_name)
            ws.append_row([item_name, qty, unit, image_url, user_id.strip().lower()])
            self._invalidate_values_cache(worksheet_name)
            logger.info(f"Added item to shopping list: {item_name}")
            return True
        except Exception as e:
            logger.error(f"Error adding item to shopping list: {e}", exc_info=True)
            return False
    
    def clear_shopping_list(self, user_id: str) -> bool:
        """
        Clear all items from the shopping list.
        
        Returns:
            True if successful, False otherwise
        """
        try:
            worksheet_name = WORKSHEET_NAMES["shopping_list"]
            ws = self._get_or_create_worksheet(worksheet_name)
            normalized_user_id = user_id.strip().lower()
            matching_rows = [
                row_number
                for row_number, row in enumerate(self._cached_values(worksheet_name, ws), start=1)
                if len(row) >= 5 and row[4].strip().lower() == normalized_user_id
            ]
            for row_number in reversed(matching_rows):
                ws.delete_rows(row_number)
            self._invalidate_values_cache(worksheet_name)
            logger.info("Shopping list cleared for user %s", normalized_user_id)
            return True
        except Exception as e:
            logger.error(f"Error clearing shopping list: {e}", exc_info=True)
            return False
    
    def delete_list_row(self, row_index: int, user_id: str) -> bool:
        """
        Delete a specific row from the shopping list.
        
        Args:
            row_index: 1-based row index within this user's list
            user_id: Stable identifier for the authenticated customer
            
        Returns:
            True if successful, False otherwise
        """
        try:
            worksheet_name = WORKSHEET_NAMES["shopping_list"]
            ws = self._get_or_create_worksheet(worksheet_name)
            normalized_user_id = user_id.strip().lower()
            matching_rows = [
                sheet_row
                for sheet_row, row in enumerate(self._cached_values(worksheet_name, ws), start=1)
                if len(row) >= 5 and row[4].strip().lower() == normalized_user_id
            ]
            if not 1 <= row_index <= len(matching_rows):
                return False
            ws.delete_rows(matching_rows[row_index - 1])
            self._invalidate_values_cache(worksheet_name)
            logger.info("Deleted shopping-list row %s for user %s", row_index, normalized_user_id)
            return True
        except Exception as e:
            logger.error(f"Error deleting row {row_index}: {e}", exc_info=True)
            return False
    
    def update_list_quantity(self, row_index: int, qty: int, user_id: str) -> bool:
        """
        Update quantity for an item in the shopping list.
        
        Args:
            row_index: 1-based row index within this user's list
            qty: New quantity
            user_id: Stable identifier for the authenticated customer
            
        Returns:
            True if successful, False otherwise
        """
        try:
            worksheet_name = WORKSHEET_NAMES["shopping_list"]
            ws = self._get_or_create_worksheet(worksheet_name)
            normalized_user_id = user_id.strip().lower()
            matching_rows = [
                sheet_row
                for sheet_row, row in enumerate(self._cached_values(worksheet_name, ws), start=1)
                if len(row) >= 5 and row[4].strip().lower() == normalized_user_id
            ]
            if not 1 <= row_index <= len(matching_rows):
                return False
            ws.update(f"B{matching_rows[row_index - 1]}", [[qty]])
            self._invalidate_values_cache(worksheet_name)
            logger.info("Updated quantity for row %s for user %s", row_index, normalized_user_id)
            return True
        except Exception as e:
            logger.error(f"Error updating quantity for row {row_index}: {e}", exc_info=True)
            return False
    
    # ========================================================================
    # RECENT SHOPS / HISTORY
    # ========================================================================

    # ========================================================================
    # CUSTOMER PRODUCT CATALOGUE
    # ========================================================================

    def save_product(self, user_id: str, title: str, image_url: str = "") -> bool:
        """Save a product a customer has used for future local searches."""
        try:
            worksheet_name = WORKSHEET_NAMES["product_catalog"]
            ws = self._get_or_create_worksheet(
                worksheet_name,
                rows=WORKSHEET_CONFIG["product_catalog"]["rows"],
                cols=WORKSHEET_CONFIG["product_catalog"]["cols"],
            )
            data = self._cached_values(worksheet_name, ws)
            headers = ["User_ID", "Title", "Image_URL", "Search_Key", "Updated"]
            if not data:
                ws.append_row(headers)
                self._invalidate_values_cache(worksheet_name)
                data = [headers]
            elif data[0] != headers:
                ws.update("A1:E1", [headers])
                self._invalidate_values_cache(worksheet_name)
                data[0] = headers

            normalized_user = user_id.strip().lower()
            normalized_title = title.strip()
            if not normalized_user or not normalized_title:
                return False

            search_key = normalized_title.lower()
            existing_row = None
            for row_number, row in enumerate(data[1:], start=2):
                if len(row) >= 4 and row[0].strip().lower() == normalized_user and row[3].strip() == search_key:
                    existing_row = row_number
                    break

            values = [normalized_user, normalized_title, image_url.strip(), search_key, datetime.now().isoformat(timespec="seconds")]
            if existing_row:
                ws.update(f"A{existing_row}:E{existing_row}", [values])
            else:
                ws.append_row(values)
            self._invalidate_values_cache(worksheet_name)
            return True
        except Exception as e:
            logger.error(f"Error saving product catalogue entry: {e}", exc_info=True)
            return False

    def search_saved_products(self, user_id: str, query: str, limit: int = 5) -> List[Dict[str, str]]:
        """Search products previously used by the current customer."""
        try:
            worksheet_name = WORKSHEET_NAMES["product_catalog"]
            ws = self._get_or_create_worksheet(
                worksheet_name,
                rows=WORKSHEET_CONFIG["product_catalog"]["rows"],
                cols=WORKSHEET_CONFIG["product_catalog"]["cols"],
            )
            normalized_user = user_id.strip().lower()
            terms = [term for term in query.strip().lower().split() if term]
            if not normalized_user or not terms:
                return []

            matches = []
            for row in self._cached_values(worksheet_name, ws)[1:]:
                if len(row) >= 4 and row[0].strip().lower() == normalized_user:
                    search_key = row[3].strip().lower()
                    if all(term in search_key for term in terms):
                        matches.append({"title": row[1].strip(), "image_url": row[2].strip()})
            return matches[:limit]
        except Exception as e:
            logger.error(f"Error searching product catalogue: {e}", exc_info=True)
            return []
    
    def archive_shop_to_history(self, items: List[List[str]], user_id: str) -> bool:
        """
        Archive current shopping list to Recent Shops worksheet.
        
        Args:
            items: List of items to archive
            user_id: Stable identifier for the authenticated customer
            
        Returns:
            True if successful, False otherwise
        """
        if not items:
            logger.warning("No valid items to archive")
            return False
        
        try:
            worksheet_name = WORKSHEET_NAMES["recent_shops"]
            history_ws = self._get_or_create_worksheet(
                worksheet_name,
                rows=WORKSHEET_CONFIG["recent_shops"]["rows"],
                cols=WORKSHEET_CONFIG["recent_shops"]["cols"],
            )
            
            history_data = self._cached_values(worksheet_name, history_ws)
            if not history_data:
                history_ws.append_row(["Item", "Qty", "Unit", "Image_URL", "Date", "User_ID"])
                self._invalidate_values_cache(worksheet_name)
            elif history_data[0][:5] == ["Item", "Qty", "Unit", "Image_URL", "Date"]:
                history_ws.update("A1:F1", [["Item", "Qty", "Unit", "Image_URL", "Date", "User_ID"]])
                self._invalidate_values_cache(worksheet_name)
            
            current_date = datetime.now().strftime(DATETIME_FORMAT)
            rows_to_add = []
            
            # Skip header row if present
            start_idx = 1 if items[0][0].lower() == "item" else 0
            for row in items[start_idx:]:
                if len(row) >= 3 and row[0].strip():
                    item_name = row[0].strip()
                    qty = row[1]
                    unit = row[2]
                    img = row[3].strip() if len(row) >= 4 else ""
                    rows_to_add.append([item_name, qty, unit, img, current_date, user_id.strip().lower()])
            
            if rows_to_add:
                history_ws.append_rows(rows_to_add)
                self._invalidate_values_cache(worksheet_name)
                logger.info(f"Archived {len(rows_to_add)} items to recent shops")
            
            return True
        except Exception as e:
            logger.error(f"Error archiving shop to history: {e}", exc_info=True)
            return False
    
    def get_recent_history(self, user_id: str, days: int = 21) -> List[Dict[str, str]]:
        """
        Get items from recent shops within the last N days.
        Removes duplicates, keeping most recent.
        
        Args:
            user_id: Stable identifier for the authenticated customer
            days: Number of days to look back
            
        Returns:
            List of unique items with their details
        """
        recent_items = {}
        
        try:
            worksheet_name = WORKSHEET_NAMES["recent_shops"]
            history_ws = self._get_or_create_worksheet(worksheet_name)
            data = self._cached_values(worksheet_name, history_ws)
            
            if len(data) <= 1:
                return []
            
            cutoff_date = datetime.now() - timedelta(days=days)
            normalized_user_id = user_id.strip().lower()
            
            # Skip header row if present
            start_idx = 1 if data[0][0].lower() == "item" else 0
            for row in data[start_idx:]:
                if len(row) >= 6 and row[5].strip().lower() == normalized_user_id:
                    item = row[0].strip()
                    if not item:
                        continue
                    
                    try:
                        row_date = datetime.strptime(row[4], DATETIME_FORMAT)
                        if row_date >= cutoff_date:
                            # Overwrite to deduplicate and keep most recent
                            recent_items[item.lower()] = {
                                "name": item,
                                "qty": row[1],
                                "unit": row[2],
                                "img": row[3],
                            }
                    except ValueError as e:
                        logger.debug(f"Skipping row with invalid date: {row}, error: {e}")
                        pass
            
            logger.info(f"Retrieved {len(recent_items)} unique recent items")
            return list(recent_items.values())
        except Exception as e:
            logger.error(f"Error getting recent history: {e}", exc_info=True)
            return []
