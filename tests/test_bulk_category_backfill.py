import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from bulk_category_backfill import (
    COLES_CATALOG_TARGETS,
    COLES_TARGETS_PER_RUN,
    get_coles_catalog_targets,
)


def test_coles_catalog_targets_start_at_dairy_and_are_bounded():
    targets = get_coles_catalog_targets({})

    assert len(targets) == COLES_TARGETS_PER_RUN
    assert targets[0] == ("full cream milk", "Dairy")
    assert targets[-1] == ("crumpets", "Bakery")


def test_coles_catalog_targets_resume_from_saved_cursor_and_wrap():
    cursor = len(COLES_CATALOG_TARGETS) - 2
    state = {("Coles", "__catalog_cursor__"): {"last_page": cursor}}

    targets = get_coles_catalog_targets(state)

    assert targets[0] == COLES_CATALOG_TARGETS[-2]
    assert targets[1] == COLES_CATALOG_TARGETS[-1]
    assert targets[2] == COLES_CATALOG_TARGETS[0]
