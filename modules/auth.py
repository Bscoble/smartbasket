"""Persistent user authentication backed by Google Sheets."""

import hashlib
import hmac
import logging
import secrets
from datetime import datetime
from typing import Any, Dict, Optional

from config import WORKSHEET_CONFIG, WORKSHEET_NAMES

logger = logging.getLogger(__name__)


class AuthManager:
    """Create users and validate persistent browser sessions."""

    _HEADERS = [
        "Email",
        "Name",
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

    def _worksheet(self) -> Any:
        try:
            worksheet = self.spreadsheet.worksheet(WORKSHEET_NAMES["users"])
        except Exception:
            worksheet = self.spreadsheet.add_worksheet(
                title=WORKSHEET_NAMES["users"],
                rows=WORKSHEET_CONFIG["users"]["rows"],
                cols=WORKSHEET_CONFIG["users"]["cols"],
            )

        worksheet_data = worksheet.get_all_values()
        if not worksheet_data:
            worksheet.append_row(self._HEADERS)
        elif worksheet_data[0][:7] == [
            "Email", "Name", "Postcode", "Password Hash",
            "Session Token Hash", "Token Created", "Created At",
        ]:
            worksheet.update("A1:H1", [self._HEADERS])
        return worksheet

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
        if len(row) < 7 or not row[0].strip():
            return None
        has_country_column = len(row) >= 8
        return {
            "email": row[0].strip().lower(),
            "name": row[1].strip(),
            "postcode": row[2].strip(),
            "country": row[3].strip() if has_country_column else "Australia",
            "password_hash": row[4] if has_country_column else row[3],
            "session_token_hash": row[5] if has_country_column else row[4],
            "has_country_column": has_country_column,
        }

    def _find_user(self, email: str) -> Optional[Dict[str, str]]:
        worksheet = self._worksheet()
        for row_number, row in enumerate(worksheet.get_all_values()[1:], start=2):
            user = self._user_from_row(row)
            if user and user["email"] == email.strip().lower():
                user["row_number"] = str(row_number)
                return user
        return None

    def create_user(
        self,
        name: str,
        email: str,
        password: str,
        postcode: str,
        country: str,
    ) -> Optional[Dict[str, str]]:
        email = email.strip().lower()
        if self._find_user(email):
            return None

        self._worksheet().append_row([
            email,
            name.strip(),
            postcode.strip(),
            country.strip(),
            self._hash_password(password),
            "",
            "",
            datetime.now().isoformat(timespec="seconds"),
        ])
        return self.authenticate(email, password)

    def authenticate(self, email: str, password: str) -> Optional[Dict[str, str]]:
        user = self._find_user(email)
        if not user or not self._verify_password(password, user["password_hash"]):
            return None

        token = secrets.token_urlsafe(32)
        worksheet = self._worksheet()
        row_number = int(user["row_number"])
        worksheet.update_cell(row_number, 5, self._hash_token(token))
        worksheet.update_cell(row_number, 6, datetime.now().isoformat(timespec="seconds"))
        user["token"] = token
        return user

    def reset_password(self, email: str, postcode: str, new_password: str) -> bool:
        """Reset a password after verifying the account email and postcode."""
        user = self._find_user(email)
        if not user or not hmac.compare_digest(user["postcode"], postcode.strip()):
            return False

        worksheet = self._worksheet()
        password_column = 5 if user["has_country_column"] else 4
        session_column = 6 if user["has_country_column"] else 5
        token_created_column = 7 if user["has_country_column"] else 6
        worksheet.update_cell(int(user["row_number"]), password_column, self._hash_password(new_password))
        # Invalidate any existing browser session after a password reset.
        worksheet.update_cell(int(user["row_number"]), session_column, "")
        worksheet.update_cell(int(user["row_number"]), token_created_column, "")
        return True

    def validate_session(self, token: str) -> Optional[Dict[str, str]]:
        if not token:
            return None

        token_hash = self._hash_token(token)
        worksheet = self._worksheet()
        for row_number, row in enumerate(worksheet.get_all_values()[1:], start=2):
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
            worksheet.update_cell(row_number, 5, "")
            worksheet.update_cell(row_number, 6, "")