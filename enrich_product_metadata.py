"""Enrich a bounded daily batch of Woolworths product-detail metadata."""

import json
import os
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

from config import (
    GOOGLE_SCOPES,
    PRODUCT_METADATA_BATCH_LIMIT,
    PRODUCT_METADATA_FAILED_RETRY_DAYS,
    PRODUCT_METADATA_MAX_AGE_DAYS,
    SPREADSHEET_ID,
)
from modules.product_metadata import fetch_woolworths_product_metadata, select_metadata_candidates
from modules.sheets import SheetsManager


def require_environment_secret(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def build_sheets_manager() -> SheetsManager:
    credentials = Credentials.from_service_account_info(
        json.loads(require_environment_secret("GCP_SERVICE_ACCOUNT")),
        scopes=GOOGLE_SCOPES,
    )
    spreadsheet = gspread.authorize(credentials).open_by_key(SPREADSHEET_ID)
    return SheetsManager(spreadsheet)


def enrich_product_metadata() -> None:
    zenrows_key = require_environment_secret("ZENROWS_KEY")
    configured_cost = os.environ.get("ZENROWS_COST_PER_REQUEST_USD", "").strip()
    try:
        cost_per_request = float(configured_cost) if configured_cost else None
    except ValueError:
        cost_per_request = None
    sheets_manager = build_sheets_manager()
    standard_prices = sheets_manager.load_standard_prices()
    existing_metadata = sheets_manager.load_product_metadata()
    candidates = select_metadata_candidates(
        standard_prices,
        existing_metadata,
        PRODUCT_METADATA_BATCH_LIMIT,
        PRODUCT_METADATA_MAX_AGE_DAYS,
        PRODUCT_METADATA_FAILED_RETRY_DAYS,
    )
    if not candidates:
        print("No Woolworths product metadata is due for enrichment.")
        return

    completed = 0
    for candidate in candidates:
        try:
            parsed, duration_secs = fetch_woolworths_product_metadata(
                candidate["source_url"],
                zenrows_key,
            )
            entry = {
                **candidate,
                **parsed,
                "last_verified": datetime.now(),
            }
            if sheets_manager.upsert_product_metadata(entry):
                completed += 1
            sheets_manager.log_scrape_run(
                source="product_metadata",
                store="Woolworths",
                query=candidate["source_url"],
                status=parsed["extraction_status"],
                duration_secs=duration_secs,
                cost_usd=cost_per_request,
            )
        except Exception as error:
            print(f"Metadata fetch failed for {candidate['source_url']}: {error}")
            sheets_manager.log_scrape_run(
                source="product_metadata",
                store="Woolworths",
                query=candidate["source_url"],
                status="failed",
                cost_usd=cost_per_request,
            )

    print(f"Completed {completed}/{len(candidates)} Woolworths metadata enrichments.")


if __name__ == "__main__":
    enrich_product_metadata()