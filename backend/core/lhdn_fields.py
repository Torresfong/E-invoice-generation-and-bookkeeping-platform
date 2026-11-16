"""Format rules for the LHDN contact fields Core stores: phone, SST number, tourism-tax number, postcode and state (#172).

`myinvois.field_rules` (#122) judges the same values when a document is mapped. `core` may not import `myinvois`
(D61), so the few rules the master data needs live here, read from the same published code lists
(`core.lhdn_codes`). The two copies must agree; the tests of both name the same limits.

Core never stores the sentinel `NA`: a blank SST or tourism-tax number means "none", and the MyInvois mapper (#123)
writes the exact text `NA` on the document. `phone_ok`, `sst_ok` and `ttx_ok` therefore refuse `NA`, in any case.
Blank values are not judged here: whether a field is required depends on the caller.
"""

import re

from .lhdn_codes import country_codes, state_codes

MALAYSIA = "MYS"
PHONE_MIN, PHONE_MAX = 8, 20
SST_MAX, TTX_MAX = 35, 17
POSTCODE_MAX, STATE_MAX = 50, 50

SST_PATTERN = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*(?:;[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)?")
TTX_PATTERN = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*")
PHONE_PATTERN = re.compile(r"\+?[0-9]+")
_MALAYSIAN_POSTCODE = re.compile(r"[0-9]{5}")


def _is_na(value):
    return value.strip().upper() == "NA"


def phone_ok(value):
    """8 to 20 characters: digits with an optional + at the start and no spaces (#122)."""
    return bool(PHONE_PATTERN.fullmatch(value)) and PHONE_MIN <= len(value) <= PHONE_MAX


def sst_ok(value):
    return len(value) <= SST_MAX and bool(SST_PATTERN.fullmatch(value)) and not _is_na(value)


def ttx_ok(value):
    return len(value) <= TTX_MAX and bool(TTX_PATTERN.fullmatch(value)) and not _is_na(value)


def country_ok(value):
    return value in country_codes()


def postal_ok(value, country):
    """A Malaysian postcode is exactly five digits; anywhere else the LHDN limit of 50 characters applies."""
    if country == MALAYSIA:
        return bool(_MALAYSIAN_POSTCODE.fullmatch(value))
    return len(value) <= POSTCODE_MAX


def state_ok(value, country):
    """A Malaysian state is a code on LHDN's list; another country may give a state name of at most 50 characters.

    "00" is refused for every country: it is no longer an LHDN state code (#122).
    """
    if value == "00":
        return False
    if country == MALAYSIA:
        return value in state_codes()
    return len(value) <= STATE_MAX
