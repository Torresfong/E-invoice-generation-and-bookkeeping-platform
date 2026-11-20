"""Proposing the daily exchange rate for a foreign-currency invoice (D24; ticket #53).

Not a fifth port (`core.ports` names four, D33): nothing else in the application reaches a rate source, and D24's
choice of source is still open for every firm but the default, so this is its own small seam instead of widening
that registry for one caller. No real adapter ships with this ticket: the firm's own source (unless it is Bank
Negara Malaysia's daily reference rate, the default) is still an open question, and building against an unverified
API contract would risk silently miscalculating money. Until a real adapter is installed, `propose_rate` always
returns None and `core.invoices` falls back to asking the owner to type the rate, which a foreign-currency invoice
always allows.
"""

from typing import Protocol


class RateSource(Protocol):
    def daily_rate(self, currency, on_date):
        """The day's reference rate for one unit of `currency` in MYR, or None when the source has none (an
        unsupported currency, a non-trading day, or an outage)."""
        ...


_source = None


def install(source):
    global _source
    _source = source


def uninstall():
    global _source
    _source = None


def propose_rate(currency, on_date):
    """The installed source's proposed rate for `currency` on `on_date`, or None when no source is installed or the
    source has none for that day."""
    if _source is None:
        return None
    return _source.daily_rate(currency, on_date)
