"""Selection logic for bounded, stale catalogue price revalidation."""

from datetime import datetime
from typing import Any, Dict, List, Tuple


def select_stale_standard_prices(
    standard_prices: Dict[Tuple[str, str], Dict[str, Any]],
    batch_limits: Dict[str, int],
    max_age_days: int,
    now: datetime = None,
) -> List[Tuple[str, str, Dict[str, Any]]]:
    """Return the oldest stale standard-price records, capped per store."""
    now = now or datetime.now()
    selected = []

    for store, limit in batch_limits.items():
        stale_entries = []
        for (entry_store, item), entry in standard_prices.items():
            if entry_store != store:
                continue
            last_verified = entry.get("last_verified")
            if not last_verified or (now - last_verified).days >= max_age_days:
                stale_entries.append((last_verified or datetime.min, item, entry))

        stale_entries.sort(key=lambda candidate: candidate[0])
        selected.extend((store, item, entry) for _verified, item, entry in stale_entries[:limit])

    return selected
