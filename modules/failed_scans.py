"""Private Google Drive storage for failed barcode captures."""

import json
import uuid
from datetime import datetime
from io import BytesIO

from google.auth.transport.requests import AuthorizedSession
from PIL import Image


DRIVE_FOLDER_NAME = "Grocery Gecko Failed Barcode Scans"
LEGACY_DRIVE_FOLDER_NAME = "SmartBasket Failed Barcode Scans"
DRIVE_FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"


def encode_failed_scan(image: Image.Image) -> bytes:
    """Normalize and compress a failed capture before private archival."""
    normalized = image.convert("RGB")
    normalized.thumbnail((1600, 1600))
    output = BytesIO()
    normalized.save(output, format="JPEG", quality=85, optimize=True)
    return output.getvalue()


class FailedScanStore:
    """Upload captures to a service-account-owned private Drive folder."""

    def __init__(self, credentials, session=None):
        self.session = session or AuthorizedSession(credentials)
        self._folder_id = None

    def _get_or_create_folder(self) -> str:
        if self._folder_id:
            return self._folder_id

        escaped_names = [
            name.replace("'", "\\'")
            for name in (DRIVE_FOLDER_NAME, LEGACY_DRIVE_FOLDER_NAME)
        ]
        response = self.session.get(
            "https://www.googleapis.com/drive/v3/files",
            params={
                "q": (
                    f"(name = '{escaped_names[0]}' or name = '{escaped_names[1]}') "
                    f"and mimeType = '{DRIVE_FOLDER_MIME_TYPE}' "
                    "and trashed = false"
                ),
                "fields": "files(id)",
                "pageSize": 1,
            },
            timeout=30,
        )
        response.raise_for_status()
        folders = response.json().get("files", [])
        if folders:
            self._folder_id = folders[0]["id"]
            return self._folder_id

        response = self.session.post(
            "https://www.googleapis.com/drive/v3/files",
            params={"fields": "id"},
            json={"name": DRIVE_FOLDER_NAME, "mimeType": DRIVE_FOLDER_MIME_TYPE},
            timeout=30,
        )
        response.raise_for_status()
        self._folder_id = response.json()["id"]
        return self._folder_id

    def upload(self, image_bytes: bytes) -> str:
        """Upload one JPEG privately and return its Drive file ID."""
        folder_id = self._get_or_create_folder()
        boundary = f"grocery-gecko-{uuid.uuid4().hex}"
        metadata = {
            "name": f"failed-barcode-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}.jpg",
            "parents": [folder_id],
        }
        body = (
            f"--{boundary}\r\n"
            "Content-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{json.dumps(metadata)}\r\n"
            f"--{boundary}\r\n"
            "Content-Type: image/jpeg\r\n\r\n"
        ).encode("utf-8") + image_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")
        response = self.session.post(
            "https://www.googleapis.com/upload/drive/v3/files",
            params={"uploadType": "multipart", "fields": "id"},
            data=body,
            headers={"Content-Type": f"multipart/related; boundary={boundary}"},
            timeout=60,
        )
        response.raise_for_status()
        return response.json()["id"]