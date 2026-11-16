"""LHDN's published code lists, kept as versioned data (tickets #121, #122, #173; D14, D59 (9), D61).

The JSON files in `lhdn_codes/` are the only copy of LHDN's own downloads from the MyInvois SDK. `manifest.json` records
the page, the file URL and the date each list was taken. LHDN changes the lists, so a refresh replaces the files and the
manifest and nothing else: no code is written into Python or into a migration. The database does not check them either,
so an old item keeps working when LHDN retires a code, and a new item is checked against the list the platform holds
today.

The item master reads three of the lists. `myinvois` reads all seven and decides what each code means (D61: `core`
imports nothing from `myinvois`, so the data and this plain loader live here and `myinvois.code_lists` re-exports them).
"""

import json
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent / "lhdn_codes"


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
    """Code to name, for example {"KGM": "kilogram"}."""
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
