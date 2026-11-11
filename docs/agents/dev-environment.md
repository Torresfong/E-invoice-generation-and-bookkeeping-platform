# Dev environment

Gotchas that no config file confesses. Read before the first command in a session.

## Running the tests

- The host has no Python dependencies. Start the stack once (`docker compose up -d --build`), then run `docker compose exec -T backend pytest <path> -q`. The `backend/` folder is mounted into the container, so an edit applies at once, with no rebuild.
- The full suite takes about 90 seconds. Run one file while working, the full suite once at the end.
- CI also runs `python check_translations.py` (every message has Chinese text and `django.mo` is the compiled `django.po`). The image has no gettext, which the check and `compilemessages` need, so install it first: `docker compose run --rm --no-deps -u root backend sh -c "apt-get update && apt-get install -y gettext && python check_translations.py"`. Read the install output: without gettext the check exits 2 instead of passing.
- The container's dev database persists between sessions and can differ from the fresh test database (old tables, un-migrated state). Trust the test run; for a migration, check it both ways: `python manage.py migrate core <previous>` then `python manage.py migrate`.

## Editing files from the shell

- Write or change code with the Write and Edit tools. Bash heredocs fail on long Python or SQL with quotes and backslashes (`unexpected EOF`), and a backslash can arrive changed (`\b` became a real backspace character).
- When a script is unavoidable, save it to a file first, then run the file.

## Git

- Stage files by explicit path. A broad `git add backend docs` also picks up untracked files that are not yours.
- Windows warns "LF will be replaced by CRLF" on every add; the warning is harmless.
