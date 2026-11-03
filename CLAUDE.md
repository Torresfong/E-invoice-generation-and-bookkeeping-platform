# CLAUDE.md

## Project state

Pilot platform, two portals. Malaysian clients issue LHDN MyInvois e-invoices and upload bookkeeping documents; the accounting firm reviews them and exports a monthly batch to SQL Account. Early build: ticket 01 (#2) foundation is in progress; most features do not exist yet.

## Source of truth 

docs/product/pilot-spec.md: user stories, decisions, out of scope.
docs/architecture/Decision/pilot-decisions.md: decisions D1 to D51. Cite the number you rely on. If a ticket or instruction contradicts one, stop and flag it; never override silently.
Issue #1 is the parent; #2 to #24 are its tickets. Ticket "NN" = issue #(NN+1).

Commands (run from `backend/`; `DATABASE_URL` must be set, there is no default because Postgres is required):

- Run everything: `docker compose up --build` from the repo root (copy `.env.example` to `.env` first).
- Check the project: `python manage.py check`
- All tests: `pytest`
- One test: `pytest core/tests/test_health.py::test_health_page_reports_app_and_database_up`
- Tests run inside the stack too: `docker compose exec backend pytest`.
- Translations: edit `backend/locale/zh_Hans/LC_MESSAGES/django.po`, then run `python manage.py compilemessages` (needs gettext). The compiled `.mo` files are committed, so recompile before committing.
- External services are reached only through `core.ports.get()`; tests get fakes from the autouse `fake_ports` fixture. Queued work runs in tests with `core.jobs.run_pending()`.
- CI runs `check`, a migration drift check and `pytest` against a Postgres 16 service (job `backend`), and builds the Docker image (job `image`). Both are required to merge.

## Workflow

1. Pick: gh issue list --repo Torresfong/acounting-saas --state open --label ready-for-agent. Take a ticket only if it has no open blockers: gh api repos/Torresfong/acounting-saas/issues/<n> --jq .issue_dependencies_summary (native dependencies, not body text). Never pick #19 or #23 without asking.
2. Read: the ticket, every decision it cites, and the matching spec section.
3. Branch: ticket-NN-<slug> from main. Never push to main: it is protected (D37), so changes arrive by pull request with the `backend` and `image` checks passing, and the rule applies to admins too.
4. Test first: write a failing test at the right seam (below), then the code.
5. Missing external input (sandbox credentials, certificate, samples, cloud account): build against the fake port and say so in the PR.
6. Verify: run the full test suite; each acceptance-criteria checkbox must be met and evidenced.
7. Review: for tickets touching tenancy, MyInvois, signing or money, get an independent review from a fresh agent before opening the PR.
8. PR: body says Closes #N, lists criteria with evidence and any decisions relied on. Don't merge.

Stop and ask when: a decision conflicts; scope would change; a migration alters row-level security; anything touches production, signing keys or real client data.

## Hard rules
- External services only through the four ports (MyInvois, AI provider, email, object storage), each a single module with a test fake. Swapping an implementation must not touch callers.
- Tenancy: every business table has a company id; Postgres RLS enforces isolation from the first migration. Never rely on application filtering alone. The pattern, role model and its limits are in docs/architecture/tenancy.md; new tenant tables use `core.rls.enable_tenant_rls`. Business tables are also put on the audit trail with `core.audit.enable_audit` (D40 (6)); entries hold field names, never values. Firm-level events go to the separate firm audit trail (D50), never into a company's trail.
- Jobs (D36, ADR 0001): job rows carry a company id under RLS and are claimed by a separate worker role, which sets the company context from that column, never from the payload (built in 02d, #34: `enqueue(name, company=...)`, the `worker` role, and `run_pending()` setting the context from the column; a job queued without a company is a system job and sees no tenant data). Payloads hold identifiers only, enforced by `enqueue()`. A failed job keeps only the exception class, and handlers must not log content.
- Roles (D17, D40, D48, D49): client owner, client staff, firm accountant, plus the firm administrator (firm-level, manages accountants, no client data). Only the owner approves and submits; no delegation. Access is by `CompanyAccess` membership under RLS, with no bypass role. Several firms are supported: a company has exactly one firm and an accountant sees only their own firm's companies. Owners self-register only through a link from their firm (D48, D49); staff come by owner invitation; firms are created only by the platform operator, with a first firm administrator who creates the firm's accountants and sees no client data. 2FA for owners, accountants and firm administrators is a go-live blocker.
- Invoices: gapless numbering per company and document type. Approval copies and locks buyer details. After go-live the platform is the client's only issuer.
- Document states: uploaded → quality-checked → classified → extracted → in review → verified → exported (plus returned, rejected). Only verified documents export, and the export includes uploaded sales invoices dated before the company's go-live date (D41). Export is a manual import; documents lock when the accountant confirms it (D43). Exported documents lock; corrections go to the next batch flagged "changed after export".
- Assistant: the model never touches the database. Four backend functions only: search customers, propose create, propose amend, save draft, run with the user's permissions. Customer writes need a confirmed before/after screen and a validated TIN. No delete, no submit. Document text is data, never instructions. Extraction output must pass schema validation.
- Secrets: signing key in a key vault, never in the repo or .env. Log every signing. Separate sandbox and production certificates. Keep .env out of git.
- Data: hosting in a Malaysian region; in-region AI preferred, not required. Firm sample documents never enter the repo.
- Money: Decimal in code, NUMERIC in the database; never floats.

## Coding principles

Think before coding:
- State important assumptions before implementation.
- If requirements have multiple plausible interpretations,
  explain the options and ask when the choice matters.
- Prefer the simplest solution that satisfies the ticket.

Simplicity:
- Do not add unrequested features, abstractions or configurability.
- Avoid speculative future-proofing and unrelated refactoring.

Surgical changes:
- Modify only what the ticket requires.
- Preserve existing conventions and patterns.
- Remove unused code introduced by your changes.
- Report unrelated issues without fixing them.

Goal-driven execution:
- Define how each acceptance criterion will be verified.
- Do not claim completion without test evide.


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
