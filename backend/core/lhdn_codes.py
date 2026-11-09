"""LHDN's published code lists, kept as data (ticket #121, D59 (9)).

The JSON files in `lhdn_codes/` are LHDN's own downloads from the MyInvois SDK (`/files/ClassificationCodes.json`,
`/files/UnitTypes.json` and `/files/TaxTypes.json`, read in November 2026). LHDN changes them from time to time, so a
refresh replaces the files and nothing else: no code is written into Python or into a migration. The database does not
check them either, so an old item keeps working when LHDN retires a code, and a new item is checked against the list
the platform holds today.
"""

import json
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).parent / "lhdn_codes"


def _load(filename, label):
    with open(DATA / filename, encoding="utf-8") as handle:
        # Descriptions in the download carry stray trailing spaces.
        return {row["Code"]: row[label].strip() for row in json.load(handle)}


@lru_cache(maxsize=None)
def classification_codes():
    """Code -> description, for example {"022": "Others"}."""
    return _load("ClassificationCodes.json", "Description")


@lru_cache(maxsize=None)
def unit_codes():
    """Code -> name, for example {"KGM": "kilogram"}."""
    return _load("UnitTypes.json", "Name")


@lru_cache(maxsize=None)
def tax_types():
    """Code -> description, for example {"01": "Sales Tax"}."""
    return _load("TaxTypes.json", "Description")
