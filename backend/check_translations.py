"""Fail when the Chinese catalog is incomplete or the committed .mo is not the compiled .po.

Run from `backend/` (CI does). It needs gettext (`xgettext`, `msgattrib`, `msgfmt`).

1. `makemessages` extracts every `_()` and `{% trans %}` string; any message with no Chinese text, or marked fuzzy, fails.
2. The committed `.po` is compiled to a temporary `.mo` and its messages must equal the committed `.mo`'s, so a
   `.po` edit that was never recompiled (the screen would still show English) fails.

`makemessages` rewrites the `.po` while it runs; the original bytes are put back afterwards, so this never changes
the working tree.
"""

import gettext
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOCALE = ROOT / "locale" / "zh_Hans" / "LC_MESSAGES"
PO, MO = LOCALE / "django.po", LOCALE / "django.mo"
TOOLS = ("xgettext", "msgattrib", "msgfmt")


def message_ids(po_text):
    """The msgids in the output of `msgattrib`, without the empty header id."""
    ids = []
    for block in re.split(r"\n\n+", po_text):
        match = re.search(r'^msgid ((?:"[^\n]*"\n?)+)', block, re.M)
        if match:
            text = "".join(re.findall(r'"(.*)"', match.group(1)))
            if text:
                ids.append(text)
    return ids


def run(*command):
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=True).stdout


def incomplete_messages():
    original = PO.read_bytes()
    try:
        run(sys.executable, "manage.py", "makemessages", "-l", "zh_Hans", "--no-location")
        untranslated = message_ids(run("msgattrib", "--untranslated", "--no-obsolete", str(PO)))
        fuzzy = message_ids(run("msgattrib", "--only-fuzzy", "--no-obsolete", str(PO)))
    finally:
        PO.write_bytes(original)
    return untranslated, fuzzy


def compiled_messages(path):
    with open(path, "rb") as handle:
        return gettext.GNUTranslations(handle)._catalog


def stale_mo():
    with tempfile.TemporaryDirectory() as folder:
        fresh = Path(folder) / "django.mo"
        run("msgfmt", "-o", str(fresh), str(PO))
        return compiled_messages(fresh) != compiled_messages(MO)


def main():
    missing_tools = [tool for tool in TOOLS if shutil.which(tool) is None]
    if missing_tools:
        print(f"check_translations needs gettext; not found: {', '.join(missing_tools)}", file=sys.stderr)
        return 2
    untranslated, fuzzy = incomplete_messages()
    problems = [f"no Chinese text: {text!r}" for text in untranslated]
    problems += [f"marked fuzzy: {text!r}" for text in fuzzy]
    if stale_mo():
        problems.append("django.mo does not match django.po: run `python manage.py compilemessages` and commit it")
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
