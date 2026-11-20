# Dev environment

Gotchas that no config file confesses. Read before the first command in a session.

## Running the tests

- The host has no Python dependencies. Start the stack once (`docker compose up -d --build`), then run `docker compose exec -T backend pytest <path> -q`. The `backend/` folder is mounted into the container, so an edit applies at once, with no rebuild.
- If `backend` is not running (startup `migrate` failed, or the stack is down), use `docker compose run --rm --no-deps backend <command>`: it builds its own test database and ignores the dev one.
- The full gate in one command: `docker compose run --rm --no-deps backend sh -c "ruff check . && python manage.py check && python manage.py makemigrations --check --dry-run && pytest -q"`.
- The full suite takes about 3.5 minutes. Run one file while working, the full suite once at the end.
- CI also runs `python check_translations.py` (every message has Chinese text and `django.mo` is the compiled `django.po`). The image has no gettext, which the check and `compilemessages` need, so install it first: `docker compose run --rm --no-deps -u root backend sh -c "apt-get update && apt-get install -y gettext && python check_translations.py"`. Read the install output: without gettext the check exits 2 instead of passing.
- After migrations are renumbered or you switch from another branch, `docker compose up` can fail in `migrate` with `column ... already exists`: the dev database applied the old numbers (`django_migrations` lists names the code no longer has). Rebuild it with `docker compose down -v` and `up -d --build`; it holds no real data.
- The container's dev database persists between sessions and can differ from the fresh test database (old tables, un-migrated state). Trust the test run; for a migration, check it both ways: `python manage.py migrate core <previous>` then `python manage.py migrate`.

## Pre-commit hook

- `scripts/git-hooks/pre-commit` runs `ruff check .`, `manage.py check`, the migration drift check and `check_translations.py` against the running `backend` container before each commit (not the full `pytest` suite, which stays CI-only). Activate it once per clone: `git config core.hooksPath scripts/git-hooks`.

## Editing files from the shell

- Write or change code with the Write and Edit tools. Bash heredocs fail on long Python or SQL with quotes and backslashes (`unexpected EOF`), and a backslash can arrive changed (`\b` became a real backspace character).
- When a script is unavoidable, save it to a file first, then run the file.

## Git

- Stage files by explicit path. A broad `git add backend docs` also picks up untracked files that are not yours.
- Windows warns "LF will be replaced by CRLF" on every add; the warning is harmless.
