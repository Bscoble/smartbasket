import os
import sys
from datetime import datetime, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.sheets import SheetsManager
from config import STANDARD_PRICE_MAX_AGE_DAYS


def test_standard_price_valid_within_max_age():
    manager = SheetsManager(spreadsheet=None)
    entry = {"price": 4.5, "last_verified": datetime.now() - timedelta(days=1)}
    assert manager.is_standard_price_valid(entry) is True


def test_standard_price_invalid_once_stale():
    manager = SheetsManager(spreadsheet=None)
    entry = {
        "price": 4.5,
        "last_verified": datetime.now() - timedelta(days=STANDARD_PRICE_MAX_AGE_DAYS + 1),
    }
    assert manager.is_standard_price_valid(entry) is False


def test_standard_price_invalid_when_missing_timestamp():
    manager = SheetsManager(spreadsheet=None)
    assert manager.is_standard_price_valid({"price": 4.5}) is False
