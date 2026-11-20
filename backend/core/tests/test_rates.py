"""Proposing the daily exchange rate for a foreign-currency invoice (D24; ticket #53).

Not a fifth port (`core.ports` names four, D33): nothing else in the application reaches a rate source, so this is
its own small seam instead of widening that registry for one caller. `core.invoices` is tested separately
(test_invoices.py) for how a draft falls back when nothing is proposed.
"""

from datetime import date
from decimal import Decimal

import pytest

from core import rates


class FakeRateSource:
    """A test decides what the source answers; `asked` records every call so a test can assert on it."""

    def __init__(self):
        self.answers = {}  # (currency, date) -> Decimal
        self.asked = []

    def daily_rate(self, currency, on_date):
        self.asked.append((currency, on_date))
        return self.answers.get((currency, on_date))


@pytest.fixture
def fake_rate_source():
    fake = FakeRateSource()
    rates.install(fake)
    yield fake
    rates.uninstall()


def test_propose_rate_is_none_with_no_source_installed():
    assert rates.propose_rate("USD", date(2026, 11, 20)) is None


def test_propose_rate_reads_the_installed_sources_answer(fake_rate_source):
    fake_rate_source.answers[("USD", date(2026, 11, 20))] = Decimal("4.47")

    assert rates.propose_rate("USD", date(2026, 11, 20)) == Decimal("4.47")
    assert fake_rate_source.asked == [("USD", date(2026, 11, 20))]


def test_propose_rate_passes_through_a_source_that_has_none_for_that_day(fake_rate_source):
    assert rates.propose_rate("USD", date(2026, 11, 21)) is None
    assert fake_rate_source.asked == [("USD", date(2026, 11, 21))]
