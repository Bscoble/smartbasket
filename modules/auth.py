"""Persistent user authentication backed by Google Sheets."""

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from config import WORKSHEET_CONFIG, WORKSHEET_NAMES
from config import SUPPORTED_COUNTRIES
from config import SHEETS_READ_CACHE_SECONDS

logger = logging.getLogger(__name__)


class AuthManager:
    """Create users and validate persistent browser sessions."""

    _HEADERS = [
        "Email",
        "First Name",
        "Surname",
        "Postcode",
        "Country",
        "Password Hash",
        "Session Token Hash",
        "Token Created",
        "Created At",
    ]
    _PBKDF2_ITERATIONS = 310_000

    def __init__(self, spreadsheet: Any):
        self.spreadsheet = spreadsheet
        self._users_worksheet: Optional[Any] = None
        self._users_values_cache: Optional[list] = None
        self._users_values_cached_at: Optional[datetime] = None
        self._headers_checked = False

    def _read_users(self, force_refresh: bool = False) -> list:
        """Return users worksheet rows with a short-lived in-memory cache."""
        if (
            not force_refresh
            and self._users_values_cache is not None
            and self._users_values_cached_at is not None
            and (datetime.now() - self._users_values_cached_at)
            < timedelta(seconds=SHEETS_READ_CACHE_SECONDS)
        ):
            return self._users_values_cache

        worksheet = self._worksheet()
        data = worksheet.get_all_values()
        self._users_values_cache = data
        self._users_values_cached_at = datetime.now()
        return data

    def _invalidate_users_cache(self) -> None:
        self._users_values_cache = None
        self._users_values_cached_at = None

    def _ensure_headers(self, worksheet: Any) -> None:
        if self._headers_checked:
            return

        worksheet_data = worksheet.get_all_values()
        if not worksheet_data:
            worksheet.append_row(self._HEADERS)
            self._invalidate_users_cache()
        elif worksheet_data[0][:7] == [
            "Email", "Name", "Postcode", "Password Hash",
            "Session Token Hash", "Token Created", "Created At",
        ]:
            worksheet.update("A1:I1", [self._HEADERS])
            self._invalidate_users_cache()
        elif worksheet_data[0][:8] == [
            "Email", "Name", "Postcode", "Country", "Password Hash",
            "Session Token Hash", "Token Created", "Created At",
        ]:
            worksheet.update("A1:I1", [self._HEADERS])
            self._invalidate_users_cache()

        self._headers_checked = True

    def _worksheet(self) -> Any:
        if self._users_worksheet is not None:
            return self._users_worksheet

        try:
            worksheet = self.spreadsheet.worksheet(WORKSHEET_NAMES["users"])
        except Exception:
            worksheet = self.spreadsheet.add_worksheet(
                title=WORKSHEET_NAMES["users"],
                rows=WORKSHEET_CONFIG["users"]["rows"],
                cols=WORKSHEET_CONFIG["users"]["cols"],
            )

        self._users_worksheet = worksheet
        self._ensure_headers(worksheet)
        return worksheet

    @staticmethod
    def _is_header_row(row: list) -> bool:
        """Return True when a row is the users worksheet header."""
        if not row:
            return False
        return row[0].strip().lower() == "email"

    @classmethod
    def _hash_password(cls, password: str, salt: Optional[bytes] = None) -> str:
        salt = salt or secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            cls._PBKDF2_ITERATIONS,
        )
        return f"{salt.hex()}${digest.hex()}"

    @classmethod
    def _verify_password(cls, password: str, stored_hash: str) -> bool:
        try:
            salt_hex, digest_hex = stored_hash.split("$", 1)
            candidate = cls._hash_password(password, bytes.fromhex(salt_hex)).split("$", 1)[1]
            return hmac.compare_digest(candidate, digest_hex)
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _user_from_row(row: list) -> Optional[Dict[str, str]]:
        if len(row) < 4 or not row[0].strip():
            return None

        if AuthManager._is_header_row(row):
            return None

        has_split_name = len(row) >= 9 and row[4].strip() in SUPPORTED_COUNTRIES
        has_country_column = len(row) >= 8 and row[3].strip() in SUPPORTED_COUNTRIES

        if has_split_name:
            first_name, surname, postcode, country = row[1], row[2], row[3], row[4]
            password_hash, session_token_hash = row[5], row[6]
            password_column, session_token_column, token_created_column = 6, 7, 8
        elif has_country_column:
            first_name, surname, postcode, country = row[1].split(" ", 1)[0], "", row[2], row[3]
            password_hash, session_token_hash = row[4], row[5] if len(row) > 5 else ""
            password_column, session_token_column, token_created_column = 5, 6, 7
        else:
            first_name = row[1].strip().split(" ", 1)[0]
            surname = row[1].strip()[len(first_name):].strip()
            postcode, country = row[2], "Australia"
            password_hash = row[3]
            session_token_hash = row[4] if len(row) > 4 else ""
            password_column, session_token_column, token_created_column = 4, 5, 6

        return {
            "email": row[0].strip().lower(),
            "name": f"{first_name.strip()} {surname.strip()}".strip(),
            "first_name": first_name.strip(),
            "surname": surname.strip(),
            "postcode": postcode.strip(),
            "country": country.strip(),
            "password_hash": password_hash,
            "session_token_hash": session_token_hash,
            "password_column": password_column,
            "session_token_column": session_token_column,
            "token_created_column": token_created_column,
        }

    @staticmethod
    def _normalize_postcode(postcode: str) -> str:
        """Normalize common Google Sheets numeric formatting for postcode matching."""
        normalized = str(postcode).strip()
        if normalized.endswith(".0"):
            normalized = normalized[:-2]
        if normalized.isdigit() and len(normalized) < 4:
            normalized = normalized.zfill(4)
        return normalized

    def _find_user(self, email: str, force_refresh: bool = False) -> Optional[Dict[str, str]]:
        for row_number, row in enumerate(self._read_users(force_refresh=force_refresh), start=1):
            user = self._user_from_row(row)
            if user and user["email"] == email.strip().lower():
                user["row_number"] = str(row_number)
                return user
        return None

    def create_user(
        self,
        first_name: str,
        surname: str,
        email: str,
        password: str,
        postcode: str,
        country: str,
    ) -> Optional[Dict[str, str]]:
        email = email.strip().lower()
        if self._find_user(email, force_refresh=True):
            return None

        self._worksheet().append_row([
            email,
            first_name.strip(),
            surname.strip(),
            postcode.strip(),
            country.strip(),
            self._hash_password(password),
            "",
            "",
            datetime.now().isoformat(timespec="seconds"),
        ])
        self._invalidate_users_cache()
        return self.authenticate(email, password)

    def authenticate(self, email: str, password: str) -> Optional[Dict[str, str]]:
        user = self._find_user(email, force_refresh=True)
        if not user or not self._verify_password(password, user["password_hash"]):
            return None

        token = secrets.token_urlsafe(32)
        worksheet = self._worksheet()
        row_number = int(user["row_number"])
        worksheet.update_cell(row_number, user["session_token_column"], self._hash_token(token))
        worksheet.update_cell(row_number, user["token_created_column"], datetime.now().isoformat(timespec="seconds"))
        self._invalidate_users_cache()
        user["token"] = token
        return user

    def reset_password(self, email: str, postcode: str, new_password: str) -> bool:
        """Reset a password after verifying the account email and postcode."""
        user = self._find_user(email, force_refresh=True)
        if not user:
            return False
        stored_postcode = self._normalize_postcode(user["postcode"])
        entered_postcode = self._normalize_postcode(postcode)
        if not hmac.compare_digest(stored_postcode, entered_postcode):
            return False

        worksheet = self._worksheet()
        worksheet.update_cell(int(user["row_number"]), user["password_column"], self._hash_password(new_password))
        # Invalidate any existing browser session after a password reset.
        worksheet.update_cell(int(user["row_number"]), user["session_token_column"], "")
        worksheet.update_cell(int(user["row_number"]), user["token_created_column"], "")
        self._invalidate_users_cache()
        return True

    def validate_session(self, token: str) -> Optional[Dict[str, str]]:
        if not token:
            return None

        token_hash = self._hash_token(token)
        for row_number, row in enumerate(self._read_users(), start=1):
            user = self._user_from_row(row)
            if user and hmac.compare_digest(user["session_token_hash"], token_hash):
                user["row_number"] = str(row_number)
                return user
        return None

    def revoke_session(self, token: str) -> None:
        user = self.validate_session(token)
        if user:
            worksheet = self._worksheet()
            row_number = int(user["row_number"])
            worksheet.update_cell(row_number, user["session_token_column"], "")
            worksheet.update_cell(row_number, user["token_created_column"], "")
            self._invalidate_users_cache()