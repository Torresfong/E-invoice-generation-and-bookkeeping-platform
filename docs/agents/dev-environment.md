# Dev environment

Gotchas that no config file confesses. Read before the first command in a session.

## Running the tests

- The host has no Python dependencies. Start the stack once (`docker compose up -d --build`), then run `docker compose exec -T backend pytest <path> -q`. The `backend/` folder is mounted into the container, so an edit applies at once, with no rebuild.
- The full suite takes about 90 seconds. Run one file while working, the full suite once at the end.
- The container's dev database persists between sessions and can differ from the fresh test database (old tables, un-migrated state). Trust the test run; for a migration, check it both ways: `python manage.py migrate core <previous>` then `python manage.py migrate`.

## Editing files from the shell

- Write or change code with the Write and Edit tools. Bash heredocs fail on long Python or SQL with quotes and backslashes (`unexpected EOF`), and a backslash can arrive changed (`\b` became a real backspace character).
- When a script is unavoidable, save it to a file first, then run the file.

## Git

- Stage files by explicit path. A broad `git add backend docs` also picks up untracked files that are not yours.
- Windows warns "LF will be replaced by CRLF" on every add; the warning is harmless.
