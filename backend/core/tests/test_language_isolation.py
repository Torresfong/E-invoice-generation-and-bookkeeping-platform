"""One test's language never reaches the next (see the `english` fixture in conftest.py).

The two tests depend on running in this order, which pytest does within a file.
"""

from django.utils import translation


def test_a_test_may_leave_another_language_active():
    translation.activate("zh-hans")  # deliberately never undone, as a request sent in Chinese leaves it


def test_the_next_test_still_starts_in_english():
    assert translation.get_language() == "en"
