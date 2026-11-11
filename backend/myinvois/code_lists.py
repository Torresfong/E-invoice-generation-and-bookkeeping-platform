"""LHDN's published code lists, kept as versioned data (ticket #122, D14, D59).

The JSON files in `codes/` are LHDN's own downloads. `manifest.json` records the page, the file URL and the date each
list was taken. LHDN changes the lists, so a refresh replaces the files and the manifest and nothing else: no code is
written into Python. The item master keeps its own copy of three of these lists in `core.lhdn_codes` (ticket #121).
That copy stays there: `core` cannot import this module (D61).
"""

import json
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent / "codes"


def code_list_sources():
    """Where each list was downloaded from, and the date it was taken."""
    return json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def _load(name):
    entry = code_list_sources()["lists"][name]
    rows = json.loads((DATA / entry["filename"]).read_text(encoding="utf-8"))
    # The downloads carry stray trailing spaces on some descriptions.
    return {row["Code"]: row[entry["label"]].strip() for row in rows}


def classification_codes():
    """Code to description, for example {"022": "Others"}."""
    return _load("classification")


def unit_codes():
    """Code to name, for example {"C62": "one"}."""
    return _load("unit")


def state_codes():
    """Code to state name. "00" is not on the list."""
    return _load("state")


def country_codes():
    """ISO country code to name, for example {"MYS": "MALAYSIA"}."""
    return _load("country")


def tax_types():
    """Code to description, for example {"01": "Sales Tax"}."""
    return _load("tax_type")


def msic_codes():
    """Five-digit MSIC code to the business-activity description LHDN prints on the document."""
    return _load("msic")


def currency_codes():
    """ISO currency code to name, for example {"MYR": "Malaysian Ringgit"}."""
    return _load("currency")
