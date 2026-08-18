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
        logger.info("SheetsManager initialized")
    
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
        try:
            return self.sh.worksheet(name)
        except gspread.WorksheetNotFound:
            logger.info(f"Creating new worksheet: {name}")
            return self.sh.add_worksheet(title=name, rows=rows, cols=cols)
    
    # ========================================================================
    # STORE PREFERENCES
    # ========================================================================
    
    def load_store_preferences(self) -> Dict[str, bool]:
        """
        Load store preferences from the Preferences worksheet.
        
        Returns:
            Dictionary of store preferences {store_name: enabled}
        """
        default_prefs = {"Woolworths": True, "Coles": True, "Aldi": True, "IGA": True}
        
        try:
            pref_ws = self._get_or_create_worksheet(WORKSHEET_NAMES["preferences"])
            data = pref_ws.get_all_values()
            
            if len(data) >= 2:
                return {
                    "Woolworths": data[1][0] == "True",
                    "Coles": data[1][1] == "True",
                    "Aldi": data[1][2] == "True",
                    "IGA": data[1][3] == "True",
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
            pref_ws = self._get_or_create_worksheet(WORKSHEET_NAMES["preferences"])
            pref_ws.update(
                "A1:D2",
                [
                    ["Woolworths", "Coles", "Aldi", "IGA"],
                    [
                        str(prefs.get("Woolworths", True)),
                        str(prefs.get("Coles", True)),
                        str(prefs.get("Aldi", True)),
                        str(prefs.get("IGA", True)),
                    ],
                ],
            )
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
            ws = self._get_or_create_worksheet(
                WORKSHEET_NAMES["price_cache"],
                rows=WORKSHEET_CONFIG["price_cache"]["rows"],
                cols=WORKSHEET_CONFIG["price_cache"]["cols"],
            )
            
            # Add header if new worksheet
            if ws.row_count == 1:
                ws.append_row(["Store", "Item", "Price", "Timestamp"])
                return cache
            
            data = ws.get_all_values()
            
            if len(data) > 1:
                for row in data[1:]:
                    if len(row) >= 4:
                        store, item, price_str, ts_str = row[0], row[1], row[2], row[3]
                        try:
                            ts = datetime.strptime(ts_str, DATETIME_TIME_FORMAT)
                            price = float(price_str)
                            cache[(store, item.lower())] = {"price": price, "timestamp": ts}
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
            ws = self._get_or_create_worksheet(
                WORKSHEET_NAMES["price_cache"],
                rows=WORKSHEET_CONFIG["price_cache"]["rows"],
                cols=WORKSHEET_CONFIG["price_cache"]["cols"],
            )
            
            rows = [["Store", "Item", "Price", "Timestamp"]]
            for (store, item), data in cache.items():
                rows.append(
                    [
                        store,
                        item,
                        str(data["price"]),
                        data["timestamp"].strftime(DATETIME_TIME_FORMAT),
                    ]
                )
            
            ws.clear()
            ws.append_rows(rows)
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
    # SHOPPING LIST
    # ========================================================================
    
    def get_shopping_list(self, user_id: str) -> List[List[str]]:
        """
        Get all items from the Shopping List worksheet.
        
        Returns:
            List of rows belonging to the authenticated customer
        """
        try:
            ws = self._get_or_create_worksheet(WORKSHEET_NAMES["shopping_list"])
            normalized_user_id = user_id.strip().lower()
            return [
                row[:4]
                for row in ws.get_all_values()
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
            ws = self._get_or_create_worksheet(WORKSHEET_NAMES["shopping_list"])
            ws.append_row([item_name, qty, unit, image_url, user_id.strip().lower()])
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
            ws = self._get_or_create_worksheet(WORKSHEET_NAMES["shopping_list"])
            normalized_user_id = user_id.strip().lower()
            matching_rows = [
                row_number
                for row_number, row in enumerate(ws.get_all_values(), start=1)
                if len(row) >= 5 and row[4].strip().lower() == normalized_user_id
            ]
            for row_number in reversed(matching_rows):
                ws.delete_rows(row_number)
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
            ws = self._get_or_create_worksheet(WORKSHEET_NAMES["shopping_list"])
            normalized_user_id = user_id.strip().lower()
            matching_rows = [
                sheet_row
                for sheet_row, row in enumerate(ws.get_all_values(), start=1)
                if len(row) >= 5 and row[4].strip().lower() == normalized_user_id
            ]
            if not 1 <= row_index <= len(matching_rows):
                return False
            ws.delete_rows(matching_rows[row_index - 1])
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
            ws = self._get_or_create_worksheet(WORKSHEET_NAMES["shopping_list"])
            normalized_user_id = user_id.strip().lower()
            matching_rows = [
                sheet_row
                for sheet_row, row in enumerate(ws.get_all_values(), start=1)
                if len(row) >= 5 and row[4].strip().lower() == normalized_user_id
            ]
            if not 1 <= row_index <= len(matching_rows):
                return False
            ws.update(f"B{matching_rows[row_index - 1]}", [[qty]])
            logger.info("Updated quantity for row %s for user %s", row_index, normalized_user_id)
            return True
        except Exception as e:
            logger.error(f"Error updating quantity for row {row_index}: {e}", exc_info=True)
            return False
    
    # ========================================================================
    # RECENT SHOPS / HISTORY
    # ========================================================================
    
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
            history_ws = self._get_or_create_worksheet(
                WORKSHEET_NAMES["recent_shops"],
                rows=WORKSHEET_CONFIG["recent_shops"]["rows"],
                cols=WORKSHEET_CONFIG["recent_shops"]["cols"],
            )
            
            history_data = history_ws.get_all_values()
            if not history_data:
                history_ws.append_row(["Item", "Qty", "Unit", "Image_URL", "Date", "User_ID"])
            elif history_data[0][:5] == ["Item", "Qty", "Unit", "Image_URL", "Date"]:
                history_ws.update("A1:F1", [["Item", "Qty", "Unit", "Image_URL", "Date", "User_ID"]])
            
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
            history_ws = self._get_or_create_worksheet(WORKSHEET_NAMES["recent_shops"])
            data = history_ws.get_all_values()
            
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
