import os
import sys
from io import BytesIO

import gspread
from PIL import Image

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.failed_scans import (
    DRIVE_FOLDER_NAME,
    LEGACY_DRIVE_FOLDER_NAME,
    FailedScanStore,
    encode_failed_scan,
)
from modules.sheets import SheetsManager


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeDriveSession:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return FakeResponse({"files": []})

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if kwargs.get("json", {}).get("mimeType"):
            return FakeResponse({"id": "private-folder-id"})
        return FakeResponse({"id": "private-image-id"})


def test_failed_scan_upload_creates_private_folder_and_multipart_image():
    session = FakeDriveSession()
    store = FailedScanStore(credentials=None, session=session)

    file_id = store.upload(b"jpeg-bytes")

    assert file_id == "private-image-id"
    assert len(session.calls) == 3
    folder_call = session.calls[1]
    assert folder_call[2]["json"] == {
        "name": DRIVE_FOLDER_NAME,
        "mimeType": "application/vnd.google-apps.folder",
    }
    upload_call = session.calls[2]
    assert upload_call[2]["params"]["uploadType"] == "multipart"
    assert b"jpeg-bytes" in upload_call[2]["data"]
    assert all("permissions" not in url for _method, url, _kwargs in session.calls)


def test_failed_scan_upload_reuses_legacy_brand_folder():
    class ExistingFolderSession(FakeDriveSession):
        def get(self, url, **kwargs):
            self.calls.append(("GET", url, kwargs))
            return FakeResponse({"files": [{"id": "legacy-folder-id"}]})

    session = ExistingFolderSession()
    store = FailedScanStore(credentials=None, session=session)

    assert store.upload(b"jpeg-bytes") == "private-image-id"
    assert len(session.calls) == 2
    folder_query = session.calls[0][2]["params"]["q"]
    assert DRIVE_FOLDER_NAME in folder_query
    assert LEGACY_DRIVE_FOLDER_NAME in folder_query
    assert b'"parents": ["legacy-folder-id"]' in session.calls[1][2]["data"]


def test_failed_scan_encoding_limits_size_and_writes_jpeg():
    image = Image.new("RGB", (2400, 1800), color="white")

    encoded = encode_failed_scan(image)
    decoded = Image.open(BytesIO(encoded))

    assert decoded.format == "JPEG"
    assert max(decoded.size) == 1600


class FakeWorksheet:
    def __init__(self):
        self.values = []
        self.row_count = 5000
        self.col_count = 7

    def get_all_values(self):
        return [row[:] for row in self.values]

    def append_row(self, row):
        self.values.append(list(row))


class FakeSpreadsheet:
    def __init__(self):
        self.worksheet_value = None

    def worksheet(self, _name):
        if self.worksheet_value is None:
            raise gspread.WorksheetNotFound("missing")
        return self.worksheet_value

    def add_worksheet(self, title, rows, cols):
        assert title == "Failed Barcode Scans"
        self.worksheet_value = FakeWorksheet()
        return self.worksheet_value


def test_failed_scan_log_links_private_file_to_customer_profile():
    spreadsheet = FakeSpreadsheet()
    manager = SheetsManager(spreadsheet)

    assert manager.log_failed_barcode_scan(
        "Customer@Example.com",
        "private-image-id",
        12345,
    )

    values = spreadsheet.worksheet_value.values
    assert values[0][0:3] == ["User ID", "Captured At", "Drive File ID"]
    assert values[1][0] == "customer@example.com"
    assert values[1][2:] == [
        "private-image-id",
        "image/jpeg",
        "12345",
        "barcode_not_detected",
        "pending",
    ]