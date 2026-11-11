# Wayfinder Migration Dry-run Report

Status: **Wayfinder activated after staged validation and Torres's explicit approval.** No issue was assigned, claimed, executed or closed; Task 6 rollback was not run.

Snapshot date: 2026-11-10, Asia/Singapore. Repository: `Torresfong/E-invoice-generation-and-bookkeeping-platform`.

## Executive result

The pre-migration `ready-for-agent` system was not a reliable Frontier signal:

- At the baseline snapshot, 29 open issues carried `ready-for-agent`.
- Only 2 of those 29 are open leaves with no open blocker and no assignee.
- 27 labelled issues are false-positive Frontier entries because they have an open child or blocker.
- 13 open issues lack `ready-for-agent` even though they are structurally eligible.
- After type-specific execution qualification, only 6 of the 15 structural candidates should enter the existing-issue Frontier.

The migration is feasible and reversible. The staged plan was run and validated, Torres approved activation, and Task 5 removed the old routing label from exactly the 29 snapshotted issues.

## Pre-migration baseline

| Measure | Observed |
|---|---:|
| Open issues | 66 |
| Parentless open issues | 6 |
| Issues with open children | 13 |
| Leaf issues | 53 |
| Issues with at least one open blocker | 41 |
| Open native dependency edges | 75 |
| Open issues with an assignee | 0 |
| Issues carrying `ready-for-agent` | 29 |
| Structural Frontier candidates | 15 |
| `ready-for-agent` issues in structural Frontier | 2 |
| Existing issues passing full dry-run qualification | 6 |

The six parentless issues are the Pilot execution root plus five orphans: the shared-certificate configuration issue, the D67 onboarding umbrella, the D66 consolidated-invoice umbrella, the real email adapter, and the real MyInvois start-up wiring issue.

## Proposed classification

| Wayfinder type | Existing open issues | Rule used |
|---|---:|---|
| `wayfinder:task` | 64 | Delivers implementation, infrastructure, operational evidence, or an execution umbrella |
| `wayfinder:research` | 1 | Security risk register and staged validation: authorized fact-finding in an isolated environment, not authorization to change production |
| `wayfinder:grilling` | 1 | Deferred firm-audit database-policy issue: D56 requires a fresh human decision before implementation |
| `wayfinder:prototype` | 0 | No current open issue has a controlled throwaway prototype as its primary deliverable |

Classification is based on each issue's primary deliverable. Mixed implementation tickets remain `task` but fail execution qualification until their stated research, access, or human prerequisite is satisfied.

## Proposed hierarchy additions

No issue that already has a parent will be moved.

| Parentless issue | Proposed parent | Reason |
|---|---|---|
| Pilot: e-invoicing and bookkeeping platform | New Wayfinder map | Execution root required by the approved migration policy |
| Allow one signing certificate reference for sandbox and production | MyInvois gateway and sandbox spike | Signing configuration belongs to the existing MyInvois workstream |
| D67: Firm and client onboarding | Pilot execution root | Newer umbrella was never attached to the pilot tree |
| D66: Consolidated e-invoices | Pilot execution root | Newer umbrella was never attached to the pilot tree |
| Real email adapter behind the EmailSender port | Invoice PDF and email | Adapter is already a native blocker of that workstream |
| Install the real MyInvois gateway at start-up and handle outages | MyInvois gateway and sandbox spike | Start-up wiring belongs to the gateway workstream |

## Dependency audit

- All 75 existing open dependency edges are preserved.
- One missing native edge is proposed: MyInvois sandbox login/TIN validation/contract tests blocks real MyInvois start-up wiring. The latter's body states this dependency, but GitHub currently does not.
- Two native guard edges are proposed:
  - A new open `wayfinder:grilling` approval gate blocks the SQL Account monthly export umbrella.
  - A new open `wayfinder:grilling` approval gate blocks onboarding, legal and production switch.
- Staged expected total: 78 open dependency edges. Validation must prove the original 75 are still a subset.

## Simulated Frontier

The following six existing issues pass all five dry-run checks: open, no open child, no open blocker, unassigned, and type-specific qualification.

| Issue | Type | Qualification result |
|---|---|---|
| Document upload with instant bounce | `wayfinder:task` | Acceptance criteria are concrete; all native blockers are closed; normal RLS/storage/TDD safeguards remain |
| Draft an invoice by form from the customer master | `wayfinder:task` | Acceptance criteria are concrete; all native blockers are closed; new tenant tables still require the existing RLS approval discipline |
| Deferred firm-audit database read restrictions before go-live | `wayfinder:grilling` | The only permitted next action is Torres's D56 decision; implementation remains forbidden |
| Security risk register and staged validation for merged PRs | `wayfinder:research` | Scope is explicitly limited to isolated, synthetic validation with no production action or client data |
| Module boundary between the invoice engine and MyInvois | `wayfinder:task` | D61 settled the layout; acceptance criteria are concrete; independent review remains mandatory |
| Client owner registers a company with no firm and starts working | `wayfinder:task` | Native blockers are closed and D67 is recorded; any unapproved RLS change still requires a stop |

After staging, the two new approval gates also appear as unassigned `wayfinder:grilling` Frontier tickets. Their presence does not approve the restricted work.

## Structural candidates held by execution qualification

These nine issues are structurally unblocked but must not enter execution Frontier yet.

| Issue | Proposed type | Hold reason |
|---|---|---|
| Malaysian hosting and staging | `wayfinder:task` | Cloud account and sending domain evidence are not recorded |
| AI port and extraction regression harness | `wayfinder:task` | The 50–100 external samples and accountant sign-off are not recorded |
| Production database roles, 2FA and session hardening | `wayfinder:task` | Combines infrastructure, implementation and fresh risk acceptance; requires cloud access and controlled decomposition |
| MyInvois sandbox login, TIN validation and contract tests | `wayfinder:task` | Authorized sandbox representation and test identities are not fully evidenced |
| Keep the signing log for 7 years | `wayfinder:task` | Production logging service/infrastructure choice and approval remain unresolved |
| LHDN code lists and required-field rules | `wayfinder:task` | Its own brief requires official-source research before implementation |
| Allow one signing certificate reference for sandbox and production | `wayfinder:task` | The issue explicitly waits for Torres's confirmation and sandbox certificate evidence |
| Real email adapter behind the EmailSender port | `wayfinder:task` | The email service and regional availability require a human decision |
| Install the real MyInvois gateway at start-up | `wayfinder:task` | Missing native dependency on the sandbox adapter issue; production settings/credentials also require approval |

## Complete existing-issue disposition

The groups below account for all 66 existing open issues: 13 non-leaf nodes + 38 blocked leaves + 6 qualified candidates + 9 held candidates.

### Non-leaf execution nodes — 13

All are `wayfinder:task` and excluded from Frontier while they have open children:

1. Pilot: e-invoicing and bookkeeping platform
2. MyInvois gateway and sandbox spike
3. Invoice draft, numbering and owner approval
4. Invoice submission and live status
5. Credit, debit and refund notes, cancellation and buyer rejection
6. SST and foreign currency
7. Classification and extraction
8. Firm portal and review queue
9. SQL Account monthly export
10. Assistant: search and draft
11. MyInvois module: map an invoice to LHDN JSON
12. D67 firm and client onboarding
13. D66 consolidated e-invoices

### Blocked leaves — 38

All are `wayfinder:task` and excluded because at least one native blocker is open:

1. Invalid invoices and MyInvois outage — 1 open blocker
2. Invoice PDF and email — 2
3. Document store for other document types — 1
4. Checklist, requests and reminders — 2
5. Client dashboard — 3
6. Assistant: propose and confirm customers — 1
7. Onboarding, legal and production switch — 9
8. Retention, exit export and pilot measures — 2
9. MyInvois sign, submit, poll and cancel — 2
10. Owner approval, buyer snapshot and lock — 1
11. Approve and submit through the MyInvois gateway — 3
12. Poll MyInvois status and show live status — 1
13. Credit, debit and refund notes — 2
14. Cancel a validated invoice — 1
15. Surface a buyer rejection request — 1
16. Per-line SST type and rate — 1
17. Foreign-currency invoices — 1
18. Classify uploads and run extraction — 2
19. Extract fields with schema validation — 1
20. Multilingual documents and original currency — 1
21. Firm portal across clients and months — 1
22. Review queue: correct and verify — 2
23. Return an unreadable document — 1
24. Export batch assembly — 2
25. Confirm import, regenerate or void — 1
26. Changed-after-export flag — 1
27. SQL Account file writer — 1
28. Voice input for the assistant — 2
29. Map a basic invoice to LHDN JSON — 4
30. Map credit, debit and refund notes to LHDN — 2
31. Map foreign currency and tax to LHDN — 3
32. Client owner invites a firm and the firm accepts — 1
33. Operator reviews the company-firm link — 1
34. Owner asks to end the firm link — 1
35. Monthly consolidated summary and rule check — 2
36. Owner approves and submits consolidated e-invoice — 4
37. Firm reviews consolidated draft — 1
38. Remove a separately issued sale from consolidation — 2

### Qualified candidates — 6

Classification and reasons are listed in **Simulated Frontier**.

### Held candidates — 9

Classification and reasons are listed in **Structural candidates held by execution qualification**.

## `ready-for-agent` discrepancy

Pre-migration label count: 29.

Only these two labelled issues are structurally eligible and pass qualification:

- Document upload with instant bounce.
- Draft an invoice by form from the customer master.

The other 27 labelled issues are excluded by open children or open dependencies. Conversely, 13 structurally eligible issues lack the label. This is the concrete reason to retire the old routing signal after staged validation.

## Special restriction verification

### SQL Account monthly export

- Currently excluded because it has four open children.
- Staged migration adds a dedicated open native approval blocker.
- Even after every child closes, it remains outside Frontier until Torres closes that gate.
- Claim preflight must reject it while the gate is open.

### Onboarding, legal and production switch

- Currently excluded by nine open native blockers.
- Staged migration adds a separate open native approval blocker.
- It remains outside Frontier even if the original nine blockers close.
- Claim preflight must reject it while the gate is open.

## Expected staged result

Before activation and before removing `ready-for-agent`:

| Measure | Expected |
|---|---:|
| Open issues | 69 |
| Existing open issues | 66 |
| New map | 1 |
| New approval gates | 2 |
| Existing `wayfinder:task` | 64 |
| Existing `wayfinder:research` | 1 |
| Existing `wayfinder:grilling` | 1 |
| New `wayfinder:grilling` gates | 2 |
| Open dependency edges | 78 |
| Issues still carrying `ready-for-agent` | 29 |
| Assigned issues | 0 |
| Existing-issue Frontier | 6 |
| New approval-gate Frontier | 2 |

Activation changes only the count of issues carrying `ready-for-agent`, from 29 to 0. It must not change issue count, classification, dependencies, parents, assignees, content, or Frontier.

## Observed staged result

Validated on 2026-11-10, Asia/Singapore, after executing only Tasks 1–4 of the approved Migration Plan.

### Counts and containment

| Measure | Expected | Observed | Result |
|---|---:|---:|---|
| Open issues | 69 | 69 | Pass |
| Existing open issues | 66 | 66 | Pass |
| Open tickets recursively under the map | 68 | 68 | Pass |
| Existing `wayfinder:task` | 64 | 64 | Pass |
| Existing `wayfinder:research` | 1 | 1 | Pass |
| Existing `wayfinder:grilling` | 1 | 1 | Pass |
| New `wayfinder:grilling` gates | 2 | 2 | Pass |
| Open dependency edges | 78 | 78 | Pass |
| Issues still carrying `ready-for-agent` | 29 | 29 | Pass |
| Assigned issues | 0 | 0 | Pass |

The live map is [Wayfinder: Pilot go-live gap map](https://github.com/Torresfong/E-invoice-generation-and-bookkeeping-platform/issues/160). Its direct children are the Pilot execution root and the two approval gates. Every other open issue reaches the map recursively through its parent chain.

### Observed Frontier

Six existing issues passed all five eligibility checks:

1. [GitHub #14 — 13: Document upload with instant bounce](https://github.com/Torresfong/E-invoice-generation-and-bookkeeping-platform/issues/14) — `wayfinder:task`.
2. [GitHub #44 — 07a: Draft an invoice by form from the customer master](https://github.com/Torresfong/E-invoice-generation-and-bookkeeping-platform/issues/44) — `wayfinder:task`.
3. [GitHub #85 — Deferred (D56): re-evaluate firm-audit database read restrictions before go-live](https://github.com/Torresfong/E-invoice-generation-and-bookkeeping-platform/issues/85) — `wayfinder:grilling`; no implementation is authorized.
4. [GitHub #100 — Security risk register and staged validation for merged PRs](https://github.com/Torresfong/E-invoice-generation-and-bookkeeping-platform/issues/100) — `wayfinder:research`; limited to the issue's isolated, synthetic scope.
5. [GitHub #128 — 03g: Module boundary: invoice engine and MyInvois module](https://github.com/Torresfong/E-invoice-generation-and-bookkeeping-platform/issues/128) — `wayfinder:task`.
6. [GitHub #143 — Client owner registers a company with no firm and starts working](https://github.com/Torresfong/E-invoice-generation-and-bookkeeping-platform/issues/143) — `wayfinder:task`.

Two new direct-map tickets also pass structurally, but they are `wayfinder:grilling` and therefore wait for Torres:

1. [Authorize direct execution of SQL Account monthly export](https://github.com/Torresfong/E-invoice-generation-and-bookkeeping-platform/issues/161).
2. [Authorize direct execution of onboarding, legal and production switch](https://github.com/Torresfong/E-invoice-generation-and-bookkeeping-platform/issues/162).

Frontier membership did not assign or start any of these tickets.

### Execution-qualification holds

Eight unblocked leaves remain outside the actionable Frontier after qualification:

1. **04: Malaysian hosting and staging** — cloud account/domain evidence absent.
2. **05: AI port and extraction regression harness** — external sample set and accountant sign-off absent.
3. **Go-live: production database roles, 2FA and session hardening** — mixed infrastructure, implementation and human-risk decisions require controlled decomposition.
4. **03b: MyInvois sandbox login, TIN validation and contract tests** — required sandbox authority/test identities are not fully evidenced.
5. **Go-live: keep the signing log for 7 years** — production logging service and infrastructure approval unresolved.
6. **03f-1: LHDN code lists and required-field rules** — the issue itself requires official-source research first.
7. **Allow one signing certificate reference for sandbox and production** — waits for explicit confirmation and sandbox certificate evidence.
8. **Real email adapter behind the EmailSender port** — email-service decision is unresolved.

**Install the real MyInvois gateway at start-up** is no longer a structural candidate: the missing native blocker from the sandbox login/contract-test issue was added as planned.

### Dependency diff

- All 75 original open dependency edges remain present.
- Exactly three open edges were added:
  - **Authorize direct execution of SQL Account monthly export** (#161) blocks **GitHub #19 (Story 18): SQL Account monthly export**.
  - **Authorize direct execution of onboarding, legal and production switch** (#162) blocks **GitHub #23 (Story 22): Onboarding, legal and production switch**.
  - **03b: MyInvois sandbox login, TIN validation and contract tests** blocks **Install the real MyInvois gateway at start-up and handle its outages on the customer screens**.
- No original dependency was removed or replaced.

### Special restriction tests

- **GitHub #19 (Story 18): SQL Account monthly export** reports its dedicated approval gate as an open native blocker. Claim preflight therefore rejects it even after its four current children close.
- **GitHub #23 (Story 22): Onboarding, legal and production switch** reports its dedicated approval gate plus its original nine open blockers. Claim preflight therefore rejects it after the original blockers close unless Torres separately closes the gate.
- Both approval gates remain open and unassigned. Creating them did not grant approval.

### Content and history integrity

Compared with `wayfinder-migration-baseline-2026-11-10.json`:

- All 66 body SHA-256 hashes match.
- All original labels are present; each issue has exactly one additive Wayfinder type label.
- All 29 `ready-for-agent` labels are still present.
- Assignees remain empty.
- Comment counts and linked PR references match.
- All existing parent links remain; only the six planned parent additions occurred.
- All original dependency edges remain; only the three planned additions occurred.

### Exact rollback operations

Rollback remains available without deleting history:

1. Remove the Wayfinder type label added to each of the original 66 issues, using the baseline classification.
2. Remove parent only from the Pilot execution root, shared-certificate configuration, D67 umbrella, D66 umbrella, real email adapter and real MyInvois start-up wiring issues.
3. Remove only the three added blocked-by relations described in **Dependency diff**.
4. Close the map and both approval gates with a rollback comment; do not delete them.
5. Re-run the baseline comparison. Expected restored existing-project state: 66 open issues, 75 open dependency edges, 6 parentless open issues, 29 `ready-for-agent` labels and 0 assignees.
6. Because activation occurred, first re-add `ready-for-agent` to exactly the 29 issue numbers recorded in the baseline snapshot.

No rollback operation has been executed.

## Observed activation result

Activated on 2026-11-10, Asia/Singapore, after Torres explicitly approved Task 5.

| Measure | Before activation | After activation | Result |
|---|---:|---:|---|
| Open issues | 69 | 69 | Unchanged |
| Open dependency edges | 78 | 78 | Unchanged |
| Existing `wayfinder:task` | 64 | 64 | Unchanged |
| Existing `wayfinder:research` | 1 | 1 | Unchanged |
| Existing `wayfinder:grilling` | 1 | 1 | Unchanged |
| New grilling approval gates | 2 | 2 | Unchanged |
| Issues carrying `ready-for-agent` | 29 | 0 | Activated as approved |
| Assigned issues | 0 | 0 | No claim occurred |
| Qualified Frontier | 8 | 8 | Unchanged |
| Execution-qualification holds | 8 | 8 | Unchanged |

Fresh integrity verification found no body, comment-count, PR-reference, parent, dependency or non-target-label drift. The activation record is posted on [Wayfinder: Pilot go-live gap map](https://github.com/Torresfong/E-invoice-generation-and-bookkeeping-platform/issues/160#issuecomment-6758863663).

The legacy `enhancement` label was intentionally retained. The approved migration plan treated it as informational only and authorized removal of `ready-for-agent` only; retiring `enhancement` requires a separate cleanup approval and is not part of Wayfinder routing.

### Post-activation clarification — 2026-11-11

A fresh live query confirmed that all eight Frontier tickets remain open, unassigned, without open children and without open native blockers. The GitHub Issue number is distinct from the story number embedded in some titles: the protected umbrellas are GitHub #19 (Story 18) and GitHub #23 (Story 22), and their dedicated gates are #161 and #162 respectively.

Frontier qualification does not give all eight the same permitted action:

- Implementation may be claimed only for task tickets #14, #44, #128 and #143.
- Authorized isolated research may be claimed for research ticket #100.
- Human decisions, not implementation, are the only next action for grilling tickets #85, #161 and #162.

## Rollback design

Rollback uses the machine-readable pre-migration snapshot and is intentionally non-destructive:

1. Re-add `ready-for-agent` to exactly the original 29 issues if activation occurred.
2. Remove only Wayfinder labels added to the original 66 issues.
3. Remove only the six parent relationships added by migration.
4. Remove only the two approval-gate dependency edges and the sandbox-to-start-up correction edge.
5. Close the map and two gate issues with a rollback explanation; do not delete them.
6. Verify the original 66 open issues, 75 open dependency edges, 6 parentless issues, label set, assignees, body hashes, comment counts and linked PR references match the baseline.

## Staged migration verdict

- **Tasks 1–4 staged migration completed and validated:** yes.
- **Task 5 activation explicitly approved and completed:** yes.
- **Remaining `ready-for-agent` labels:** 0.
- **Any Frontier ticket claimed or executed by migration:** no.
- **Task 6 rollback executed:** no.
- **Next operation:** select or resolve a Wayfinder Frontier ticket in a separate, explicit action; activation alone authorizes none.
