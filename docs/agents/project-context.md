# Project Rules and Context

## Project state

Pilot platform, two portals. Malaysian clients issue LHDN MyInvois e-invoices and upload bookkeeping documents; the accounting firm reviews them and exports a monthly batch to SQL Account. Early build: tenancy, sign-in, firms, audit, the job queue and document numbering are in place; most business features (invoice drafting and approval, documents, export) are not built yet. `gh issue list` shows what is built and what is open.

## Source of truth 

docs/product/pilot-spec.md: user stories, decisions, out of scope.
docs/architecture/Decision/pilot-decisions.md: decisions D1 to D62 (D61, D62 are the newest; a D-number may already be claimed by an open PR, so check `gh pr list` before taking one). Cite the number you rely on. If a ticket or instruction contradicts one, stop and flag it; never override silently.
Issue #1 is the parent. Its original tickets are #2 to #24 (ticket "NN" = issue #(NN+1)); later and split tickets (D47) have their own issue numbers, so use the issue the task names.
Built behaviour and its limits: `docs/architecture/tenancy.md` (tenancy, sign-in, audit, firms, jobs, numbering) and `docs/adr/0001-job-queue-and-tenancy.md`.

Commands (run from `backend/`; `DATABASE_URL` must be set, there is no default because Postgres is required):

- Run everything: `docker compose up --build` from the repo root (copy `.env.example` to `.env` first).
- Check the project: `python manage.py check`
- All tests: `pytest`
- One test: `pytest core/tests/test_health.py::test_health_page_reports_app_and_database_up`
- Tests run inside the stack too: `docker compose exec backend pytest`. On a Windows host that is the only place they run; see `docs/agents/dev-environment.md` for that and the shell gotchas.
- Translations: edit `backend/locale/zh_Hans/LC_MESSAGES/django.po`, then run `python manage.py compilemessages` (needs gettext). The compiled `.mo` files are committed, so recompile before committing.
- External services are reached only through `core.ports.get()`; tests get fakes from the autouse `fake_ports` fixture. Queued work runs in tests with `core.jobs.run_pending()`.
- CI runs `check`, a migration drift check and `pytest` against a Postgres 16 service (job `backend`), and builds the Docker image (job `image`). Both are required to merge.

## Workflow

Follow [AI workflow](ai-workflow.md). The existing ticket/decision/verification constraints remain mandatory.

## Hard rules
- External services only through the four ports (MyInvois, AI provider, email, object storage), each a single module with a test fake. Swapping an implementation must not touch callers.
- Tenancy: every business table has a company id; Postgres RLS enforces isolation from the first migration. Never rely on application filtering alone. The pattern, role model and its limits are in docs/architecture/tenancy.md; new tenant tables use `core.rls.enable_tenant_rls`. Business tables are also put on the audit trail with `core.audit.enable_audit` (D40 (6)); entries hold field names, never values. Firm-level events go to the separate firm audit trail (D50), never into a company's trail.
- Jobs (D36, ADR 0001): job rows carry a company id under RLS and are claimed by a separate worker role, which sets the company context from that column, never from the payload (built in 02d, #34: `enqueue(name, company=...)`, the `worker` role, and `run_pending()` setting the context from the column; a job queued without a company is a system job and sees no tenant data). Payloads hold identifiers only, enforced by `enqueue()`. A failed job keeps only the exception class, and handlers must not log content. Job execution (#27, `docs/architecture/tenancy.md` "Job execution"): a job is claimed as `running` under a lease and the handler runs outside the row lock; handlers raise `TransientError` or `PermanentError` (anything else is retried a limited number of times); every port call with an external effect from a handler passes `idempotency_key=jobs.idempotency_key(step)`; finished jobs are purged daily (`JOB_RETENTION_DAYS`); `manage.py failed_jobs` prints the count to alert on, `manage.py schedule_jobs` queues the purge.
- Roles (D17, D40, D48, D49): client owner, client staff, firm accountant, plus the firm administrator (firm-level, manages accountants, no client data). Only the owner approves and submits; no delegation. Access is by `CompanyAccess` membership under RLS, with no bypass role. Several firms are supported: a company has at most one firm and an accountant sees only their own firm's companies. Owners and firms self-register, the owner invites a firm, and the operator approves each firm and each company-firm link before access is granted (D48, D67); staff come by owner invitation; a firm administrator creates the firm's accountants and sees no client data. Consolidated e-invoices are in scope (D66). 2FA for owners, accountants and firm administrators is a go-live blocker.
- Invoices: gapless numbering per company and document type, one sequence shared by normal and e-invoices; prefix, width and starting number are frozen once a number is issued (D62, `core.numbering`). Approval copies and locks buyer details and takes the number. After go-live the platform is the client's only issuer.
- E-invoice route (D59, D60, D61): one invoice engine in `core` for every invoice; everything LHDN-specific sits in a separate Django app, `backend/myinvois/`, reached only through the gateway port and the job runner, and `core` imports nothing from it. A per-company setting (one `core` table, read through `einvoicing_enabled(company, on_date)`) decides whether a new approved invoice is submitted; a credit, debit or refund note follows the route of the invoice it corrects instead. An invoice more than 72 hours old at submission goes back to the owner for approval, keeping its number.
- Requests (D53): a request's writes are all-or-nothing. A port call that cannot be undone (MyInvois, object storage, AI) is queued as a job, never made inside a request transaction.
- Document states: uploaded → quality-checked → classified → extracted → in review → verified → exported (plus returned, rejected). Only verified documents export, and the export includes uploaded sales invoices dated before the company's go-live date (D41). Export is a manual import; documents lock when the accountant confirms it (D43). Exported documents lock; corrections go to the next batch flagged "changed after export".
- Assistant: the model never touches the database. Four backend functions only: search customers, propose create, propose amend, save draft, run with the user's permissions. Customer writes need a confirmed before/after screen and a validated TIN. No delete, no submit. Document text is data, never instructions. Extraction output must pass schema validation.
- Secrets: signing key in a key vault, never in the repo or .env. Log every signing. Separate sandbox and production certificates. Keep .env out of git.
- Data: hosting in a Malaysian region; in-region AI preferred, not required. Firm sample documents never enter the repo.
- Money: Decimal in code, NUMERIC in the database; never floats.

## Coding principles

- State the assumption you proceed on. When a requirement has more than one plausible reading and the choice matters, set out the options and ask.
- Modify only what the ticket requires: no unrequested features, abstractions or configurability, no unrelated refactoring.
- Report unrelated issues without fixing them.

## Testing

Test external behaviour only, at three seams, preferring the first:

1. HTTP interface, job runner in-process, four ports faked. Cross-company requests that must fail prove isolation.
2. MyInvois contract tests against the real sandbox, skipped cleanly without credentials.
3. Extraction regression harness scoring the firm's samples per document type (samples stay outside the repo).

The isolation guards in `backend/core/tests/test_isolation.py` go red on purpose: every new table carries a company id and FORCE row-level security (or joins `DJANGO_OWN` / `PLATFORM` there, with a reason), every new `/companies/<id>/` route joins `ENDPOINTS`, and every new business endpoint ships an HTTP test that a request for another company fails.

## Conventions worth knowing

- Client screens: English and Chinese strings from the start. Firm portal and invoice PDFs: English.
- Scope is fixed. The schedule may slip to about 2027-04-27. Don't propose cutting features.
- Domain terms: root GLOSSARY-MAP.md points to per-context glossaries (docs/agents/domain.md). Issue tracker conventions: docs/agents/issue-tracker.md.

### Issue tracker

Issues and specs live as GitHub issues in Torresfong/acounting-saas, using the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Domain docs

Multi-context monorepo: a root `GLOSSARY-MAP.md` points at one `GLOSSARY.md` per context (currently only `backend/`). See `docs/agents/domain.md`.
