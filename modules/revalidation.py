"""Selection logic for bounded, stale catalogue price revalidation."""

from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Tuple

from modules.catalog_matching import find_local_price_matches


def select_stale_standard_prices(
    standard_prices: Dict[Tuple[str, str], Dict[str, Any]],
    batch_limits: Dict[str, int],
    max_age_days: int,
    now: datetime = None,
    shopping_item_names: Iterable[str] = (),
) -> List[Tuple[str, str, Dict[str, Any]]]:
    """Prioritize stale shopping-list matches, then oldest entries, within store caps."""
    now = now or datetime.now()
    selected = []

    def is_fresh(entry: Dict[str, Any]) -> bool:
        verified = entry.get("last_verified")
        return bool(verified and now - verified < timedelta(days=max_age_days))

    priority_keys = set()
    for item_name in dict.fromkeys(shopping_item_names):
        stores = list(batch_limits)
        item_lower = item_name.lower()
        for store in stores:
            if store.lower() in item_lower:
                stores = [store]
                break
        fresh_matches = find_local_price_matches(
            item_name, stores, standard_prices, is_fresh,
        )
        stale_matches = find_local_price_matches(
            item_name,
            [store for store in stores if store not in fresh_matches],
            standard_prices,
            lambda entry: not is_fresh(entry),
        )
        priority_keys.update(key for key, _entry in stale_matches.values())

    for store, limit in batch_limits.items():
        stale_entries = []
        for (entry_store, item), entry in standard_prices.items():
            if entry_store != store:
                continue
            last_verified = entry.get("last_verified")
            if not is_fresh(entry):
                stale_entries.append((last_verified or datetime.min, item, entry))

        stale_entries.sort(key=lambda candidate: (
            (store, candidate[1]) not in priority_keys,
            candidate[0],
        ))
        selected.extend((store, item, entry) for _verified, item, entry in stale_entries[:limit])

    return selected
