# Coding standards

Read by the reviewer (`/code-review`), not loaded into every session. Judgement calls only: what a tool can check (unused code, bad escapes, control characters) runs in CI as `ruff check`; tenancy rules live in `docs/architecture/tenancy.md`.

## A write and the call that follows it share one outcome

A port call (email, MyInvois, storage, AI) that must follow a database write goes inside the transaction that makes the write, so a failure rolls both back, or goes through the job queue. Django turns a view's exception into a 500 response before the request's own transaction sees it, so that transaction commits (#86). Seen in `firm_admin.add` and the `create_firm` command (fixed in #83); still open in `invite_staff`, `request_password_reset` and `create_accountant`.

## A SECURITY DEFINER function checks the caller after it holds the lock

Take the firm (or company) lock first, then check that the caller is still allowed. A check made before the lock lets an administrator deactivated while waiting go on to act. The firm or company comes from the caller's own row, never from an argument (migration 0026).

## Database errors are matched by SQLSTATE

Branch on `error.__cause__.sqlstate`, with the code named as a constant next to the function that raises it. A message string changes with a wording edit and the branch stops firing without a failing test.

## A test that asserts a refusal is shown to fail once

Break the code it guards, watch the test go red, restore it. Pair a guard test (a route list, a schema check) with a test that feeds the guard bad input. A refusal test also needs a positive control: the same call on something the caller may touch succeeds, so the refusal is the rule and not a broken route. Seen: a route-guard regex that matched nothing (#83), and a create/update test that passed whatever the database did (#87).

## Fixtures shared by several test modules live in `conftest.py`

Import a fixture from another test module only for a plain helper function.
