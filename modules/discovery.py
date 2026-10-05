"""Customer-independent demand queue for offline catalogue discovery."""

import re
from datetime import datetime, timedelta

from config import STORE_NAMES
from modules.catalog_matching import find_local_price_matches


QUEUE_NAME = "Discovery Queue"
QUEUE_HEADERS = [
    "Store", "Query", "Requested At", "Attempts", "Last Attempt", "Status",
    "Last Error", "Next Attempt",
]
MAX_ATTEMPTS = 3
RETRY_COOLDOWN = timedelta(days=1)


def normalize_query(query):
    """Accept a product description, never account/contact data or URLs."""
    if not isinstance(query, str):
        return ""
    query = " ".join(query.split()).strip()
    if not query or "@" in query or re.search(r"https?://", query, re.I):
        return ""
    return query[:200]


def requested_stores(query, stores=STORE_NAMES):
    mentioned = [
        store for store in STORE_NAMES
        if re.search(rf"\b{re.escape(store)}\b", query, re.I)
    ]
    return mentioned if mentioned else list(stores)


def _timestamp(value):
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


class DiscoveryQueue:
    """Persist only store/product demand and bounded retry state."""

    def __init__(self, sheets_manager):
        self.sheets_manager = sheets_manager

    def _worksheet(self):
        return self.sheets_manager._get_or_create_worksheet(
            QUEUE_NAME, rows="2000", cols="8",
        )

    def load(self):
        worksheet = self._worksheet()
        values = worksheet.get_all_values()
        if not values:
            worksheet.append_row(QUEUE_HEADERS, value_input_option="RAW")
            return []
        if values[0] != QUEUE_HEADERS:
            raise RuntimeError("Discovery Queue has unexpected headers")
        requests = []
        seen = set()
        for row_number, raw in enumerate(values[1:], 2):
            row = (raw + [""] * 8)[:8]
            query = normalize_query(row[1])
            key = (row[0], query.casefold())
            if row[0] not in STORE_NAMES or not query or key in seen:
                continue
            seen.add(key)
            try:
                attempts = max(0, int(row[3] or 0))
            except ValueError:
                attempts = MAX_ATTEMPTS
            requests.append({
                "row": row_number, "store": row[0], "query": query,
                "requested_at": row[2], "attempts": attempts,
                "last_attempt": row[4], "status": row[5] or "pending",
                "last_error": row[6], "next_attempt": row[7],
            })
        return requests

    def enqueue(self, queries, stores=STORE_NAMES, now=None):
        now = now or datetime.now()
        requests = self.load()
        seen = {(item["store"], item["query"].casefold()) for item in requests}
        rows = []
        for raw_query in queries:
            query = normalize_query(raw_query)
            if not query:
                continue
            for store in requested_stores(query, stores):
                key = (store, query.casefold())
                if key in seen:
                    continue
                seen.add(key)
                rows.append([
                    store, query, now.isoformat(timespec="seconds"), "0", "",
                    "pending", "", "",
                ])
        if rows:
            self._worksheet().append_rows(rows, value_input_option="RAW")
        return len(rows)

    def update(self, request, **changes):
        updated = {**request, **changes}
        row = [
            updated["store"], updated["query"], updated["requested_at"],
            str(updated["attempts"]), updated["last_attempt"], updated["status"],
            updated["last_error"], updated["next_attempt"],
        ]
        self._worksheet().update(
            range_name=f"A{request['row']}:H{request['row']}",
            values=[row], value_input_option="RAW",
        )
        request.update(changes)

    def due(self, now=None, batch_size=15):
        now = now or datetime.now()
        selected = []
        for request in self.load():
            if request["status"] not in {"pending", "retry"}:
                continue
            if request["attempts"] >= MAX_ATTEMPTS:
                self.update(request, status="exhausted", next_attempt="")
                continue
            next_attempt = _timestamp(request["next_attempt"])
            last_attempt = _timestamp(request["last_attempt"])
            if next_attempt and next_attempt > now:
                continue
            if last_attempt and now - last_attempt < RETRY_COOLDOWN:
                continue
            selected.append(request)
        return selected[:max(0, batch_size)]


def missing_catalogue_requests(queries, standard_prices, stores=STORE_NAMES):
    """Existing matches, including stale ones, belong to revalidation instead."""
    missing = {}
    for raw_query in queries:
        query = normalize_query(raw_query)
        if not query:
            continue
        targets = requested_stores(query, stores)
        matches = find_local_price_matches(
            query, targets, standard_prices, lambda entry: True,
        )
        for store in targets:
            if store not in matches:
                missing.setdefault(store, {}).setdefault(query.casefold(), query)
    return {store: list(queries.values()) for store, queries in missing.items()}
