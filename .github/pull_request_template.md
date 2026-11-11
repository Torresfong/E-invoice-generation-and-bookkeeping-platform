## Issue and intent
Closes #

## Implementation
- Developer (Claude Code / Codex / human):
- Scope and affected components:
- Relevant product decisions (D-numbers):

## Acceptance criteria and evidence
- [ ] Criterion / evidence:

## Verification
- `python manage.py check`: 
- `python manage.py makemigrations --check --dry-run`:
- `pytest`:
- CI `backend` / `image`:
- External sandbox checks (or why unavailable):

## Risk and handoff
- Tenant/RLS, money, auth/session, signing or MyInvois impact:
- Database migrations / rollback:
- Known limitations and go-live-only requirements:
- Current HEAD SHA / outstanding work:

## Test coverage and architecture consistency
- Existing tests inspected; new behavior/failure modes covered without duplicating scenarios:
- Test seams used (HTTP / Jobs / External Ports / sandbox / extraction):
- New abstractions or refactors and relevant ADR justification (or `none`):

## AI handoff (complete when changing developer)
- Previous developer / incoming developer:
- Issue, branch and exact HEAD SHA:
- Completed acceptance criteria:
- Remaining acceptance criteria / next action:
- Related decisions (D-numbers):
- Test commands with PASS / FAIL / NOT RUN and limitations:
- Known risks, migrations and blockers:
- Incoming developer verified Issue, PR, commits, diff, tests and actual repository state: [ ]

## Independent review
- [ ] Pre-PR independent review completed, if tenancy/MyInvois/signing/money changed
- [ ] Fresh independent review completed on current HEAD: actionable findings are published as PR comments, or an exact-HEAD completed receipt explicitly reports no findings and no unresolved findings
- [ ] Actionable PR review comments resolved on the feature branch, and updated HEAD freshly re-reviewed until no actionable findings remain
- [ ] Any finding still unresolved after three fix attempts is documented, the PR is Draft, and Torres has been asked to inspect it

> Do not merge automatically. Torres makes the final merge decision.
