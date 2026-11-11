# Wayfinder Issue Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate every existing open GitHub issue into a staged, reversible Wayfinder execution system, validate the result, and retire `ready-for-agent` only after Torres gives a second explicit approval.

**Architecture:** A new `wayfinder:map` issue becomes the control plane. The existing Pilot issue remains the execution root, existing issue bodies and relationships are preserved, and previously parentless issues are attached without moving any issue that already has a parent. Wayfinder classification is additive during staging; activation removes only `ready-for-agent` after validation and approval.

**Tech Stack:** GitHub Issues, native GitHub sub-issues and dependencies, GitHub CLI 2.94+, PowerShell, repository Markdown documentation.

**Spec:** `docs/reports/wayfinder-migration-dry-run-2026-11-10.md` and Torres's ten migration conditions recorded in the originating Codex conversation.

## Global Constraints

- The migration is staged: create and validate the Wayfinder structure before removing `ready-for-agent`.
- Do not delete, overwrite, regenerate, or rewrite existing issue bodies, specifications, acceptance criteria, comments, architecture decisions, PR history, or native dependencies.
- Classify implementation as `wayfinder:task`, fact-finding as `wayfinder:research`, human decisions as `wayfinder:grilling`, and controlled prototypes as `wayfinder:prototype`.
- Frontier requires: open issue, no open child issue, no open blocker, no assignee, and a passed type-specific execution-qualification check.
- Assignment is the only claim mechanism. Frontier membership never assigns or starts work.
- Research, grilling, prototype, and task execution retain the limits in the approved migration conditions.
- The execution restrictions on GitHub Issue #19 (Story 18, SQL Account monthly export) and GitHub Issue #23 (Story 22, onboarding/legal/production switch) must be enforced with native open blocker issues, not only prose.
- Wayfinder execution never bypasses human decisions, acceptance criteria, PR review, required tests, security gates, or deployment approval.
- Do not modify existing closed issues.
- Stop after staged validation and obtain Torres's confirmation before activation.
- Do not remove legacy `enhancement` or `bug` labels in this migration. They are informational only; Wayfinder labels control routing. Removing them requires a separate approval.
- Do not select or execute GitHub Issue #19 or GitHub Issue #23 during migration.

## Review Focus

- A parent change must never detach an issue that already has a parent; only the six currently parentless issues may receive a parent.
- The original 75 open dependency edges must remain a subset of the staged graph; only three approved guard/correction edges may be added.
- `ready-for-agent` must remain on all 29 current issues until staged validation passes and Torres explicitly authorizes activation.
- The simulated Frontier must exclude every umbrella, blocked issue, assigned issue, and execution-qualification hold, especially the two specially restricted issues.
- Rollback must restore labels, parents, and dependencies from the pre-migration snapshot without deleting the map or its audit trail.

---

### Task 1: Capture an immutable pre-migration baseline

**Files:**
- Create during execution: `docs/reports/wayfinder-migration-baseline-2026-11-10.json`
- Verify against: `docs/reports/wayfinder-migration-dry-run-2026-11-10.md`

**Interfaces:**
- Consumes: GitHub's current open issue graph.
- Produces: a machine-readable rollback source containing issue number, title, body hash, labels, assignees, parent, sub-issues, blocked-by edges, blocking edges, comment count, and linked PR references.

- [ ] **Step 1: Export all existing open issues with their complete migration metadata**

  Use one read-only GraphQL query against `Torresfong/E-invoice-generation-and-bookkeeping-platform`. Store no credentials, comment bodies, client data, or token values.

- [ ] **Step 2: Hash every existing open issue body**

  Record SHA-256 hashes so staged validation can prove specifications and acceptance criteria were not rewritten.

- [ ] **Step 3: Verify the baseline invariants**

  Expected before any mutation:

  - 66 open issues.
  - 6 parentless open issues.
  - 13 open issues with open children and 53 leaves.
  - 75 open dependency edges affecting 41 issues.
  - 0 assigned open issues.
  - 29 issues with `ready-for-agent`.
  - 15 structural Frontier candidates, but only 6 existing issues pass type-specific qualification.

- [ ] **Step 4: Stop if the live values differ**

  Refresh this plan and the dry-run report instead of applying stale mutations.

### Task 2: Create the Wayfinder control plane without changing existing issues

**GitHub artifacts:**
- Create: one issue labelled `wayfinder:map`.
- Create: one `wayfinder:grilling` approval gate for the SQL Account export umbrella.
- Create: one `wayfinder:grilling` approval gate for onboarding/legal/production switch.

**Interfaces:**
- Consumes: the approved Destination, Notes, classification rules, and restrictions.
- Produces: an open map and two open human-decision gates. No existing issue changes occur in this task.

- [ ] **Step 1: Ensure the five Wayfinder labels exist**

  Create only missing labels: `wayfinder:map`, `wayfinder:task`, `wayfinder:research`, `wayfinder:grilling`, and `wayfinder:prototype`. Do not edit or delete existing labels.

- [ ] **Step 2: Create the map**

  Destination: a dependency-correct, decision-complete route from the current repository state to the complete Malaysian pilot and its go-live gates.

  Notes must state:

  - Execution is explicitly in scope.
  - The Pilot issue is the execution root and recursive leaf issues are eligible for Frontier evaluation.
  - Legacy labels are ignored for routing during the staged period.
  - Assignee is the claim.
  - The four issue-type execution limits and all repository PR/test/security rules remain binding.
  - The two restricted issues require their native approval blockers to be closed by Torres.

- [ ] **Step 3: Create the two approval gates as direct map children**

  Each body contains one question: whether Torres authorizes direct execution/claiming of the named restricted issue. Leave both open and unassigned.

- [ ] **Step 4: Verify create-only behavior**

  Confirm the original 66 issues have unchanged labels, parents, dependencies, assignees, bodies, comments, and PR references.

### Task 3: Stage classification, hierarchy, and guard edges

**GitHub artifacts modified:**
- Existing 66 open issues: add exactly one Wayfinder type label.
- Existing parentless issues: add only the parents listed in the dry-run report.
- Native dependencies: add only three edges described below.

**Interfaces:**
- Consumes: baseline JSON and the classification table in the dry-run report.
- Produces: a fully classified but not yet activated graph; every current `ready-for-agent` label remains present.

- [ ] **Step 1: Add the proposed Wayfinder classification**

  Expected existing-issue totals:

  - `wayfinder:task`: 64.
  - `wayfinder:research`: 1, the security risk register and staged validation issue.
  - `wayfinder:grilling`: 1, the deferred firm-audit database-policy decision.
  - `wayfinder:prototype`: 0.

  Add labels only. Do not remove `ready-for-agent`, `enhancement`, or any other current label.

- [ ] **Step 2: Attach the execution tree without moving existing children**

  Apply only these parent additions:

  - Pilot issue -> Wayfinder map.
  - Shared-certificate configuration issue -> MyInvois gateway umbrella.
  - D67 onboarding umbrella -> Pilot issue.
  - D66 consolidated e-invoice umbrella -> Pilot issue.
  - Real email adapter -> Invoice PDF and email umbrella.
  - Real MyInvois start-up wiring -> MyInvois gateway umbrella.

  Before each write, verify the child still has no parent. Stop on any mismatch.

- [ ] **Step 3: Add the two special approval blockers**

  Add the SQL Account approval gate as a native blocker of the SQL Account export umbrella. Add the onboarding approval gate as a native blocker of onboarding/legal/production switch.

- [ ] **Step 4: Correct one missing native dependency**

  Add the MyInvois sandbox login/contract-test issue as a native blocker of the real MyInvois start-up wiring issue. Its body already says it needs the adapter from that issue; this step makes the dependency visible to Frontier computation.

- [ ] **Step 5: Prove no existing edge or content changed**

  Compare against the baseline: all 75 original open dependency edges remain, the three planned edges are the only additions, and all body hashes/comment counts/PR references are unchanged.

### Task 4: Run staged migration validation and stop for approval

**Files:**
- Update: `docs/reports/wayfinder-migration-dry-run-2026-11-10.md` with an “Observed staged result” section.

**Interfaces:**
- Consumes: staged GitHub graph and baseline JSON.
- Produces: the approval packet Torres requires before activation.

- [ ] **Step 1: Verify staged counts**

  Expected if no unrelated issue changed concurrently:

  - 69 open issues: 66 existing, 1 map, 2 approval gates.
  - 68 open tickets under the map, excluding the map itself.
  - Existing classification: 64 task, 1 research, 1 grilling.
  - New gates: 2 grilling.
  - 78 open dependency edges: 75 original plus 3 planned additions.
  - 29 `ready-for-agent` labels still present.
  - 0 automatic assignments.

- [ ] **Step 2: Compute the simulated Frontier**

  Expected existing-issue Frontier:

  - Document upload with instant bounce.
  - Draft an invoice by form from the customer master.
  - Deferred firm-audit database read restriction decision.
  - Security risk register and staged validation.
  - Invoice engine/MyInvois module boundary.
  - Client owner registers a company without a firm.

  The two new approval gates are also unassigned grilling Frontier tickets. They wait for Torres; they do not authorize the restricted issues merely by existing.

- [ ] **Step 3: Verify the two restricted issues**

  Both must report at least one open native blocker even if all original product blockers later close. A claim preflight must reject either issue while its approval gate remains open.

- [ ] **Step 4: Publish the full validation packet**

  Include before/after counts, per-type classification, Frontier, held candidates with reasons, complete dependency diff, special restriction tests, content-integrity checks, and rollback commands.

- [ ] **Step 5: Hard stop**

  Do not remove `ready-for-agent`, assign an issue, start a research ticket, run a prototype, or execute a task. Request Torres's explicit activation approval.

### Task 5: Activate Wayfinder only after Torres approves the staged result

**GitHub artifacts modified:**
- The 29 issues in the baseline snapshot that currently carry `ready-for-agent`.

**Interfaces:**
- Consumes: Torres's explicit approval of the observed staged validation packet.
- Produces: an active Wayfinder execution system with no issue carrying `ready-for-agent`.

- [ ] **Step 1: Re-read the live graph and repeat Task 4 checks**

  Stop on drift; do not apply a stale removal list.

- [ ] **Step 2: Remove `ready-for-agent` only from the snapshotted 29 issues**

  Do not delete the repository label and do not remove `enhancement`, `bug`, Wayfinder, or any other label.

- [ ] **Step 3: Verify activation**

  Confirm zero open issues carry `ready-for-agent`, classification counts are unchanged, no issue was assigned, and the Frontier is identical to the approved simulation except for legitimate concurrent changes.

- [ ] **Step 4: Record activation on the map**

  Add a dated context pointer to the approved validation report and Torres's approval. Do not claim any Frontier ticket.

### Task 6: Roll back without deleting history

**Interfaces:**
- Consumes: the baseline JSON and the list of migration-created artifacts/edges.
- Produces: the exact pre-migration state for existing issues, plus closed migration artifacts retained for audit.

- [ ] **Step 1: Stop all migration activity and verify no ticket was claimed**

- [ ] **Step 2: Restore labels from the baseline**

  Re-add `ready-for-agent` to exactly the original 29 issues if activation occurred. Remove only Wayfinder labels added to the original 66 issues.

- [ ] **Step 3: Restore parents**

  Remove the map parent from the Pilot issue and remove only the five parent relationships added to previously parentless issues. Do not touch any pre-existing parent relationship.

- [ ] **Step 4: Restore dependencies**

  Remove only the two approval-gate edges and the sandbox-to-start-up correction edge. Prove the original 75 open dependency edges still match the baseline.

- [ ] **Step 5: Preserve the migration audit trail**

  Close the map and two gate issues with a rollback comment; do not delete them. This returns the existing-project open count to 66 while retaining what happened.

- [ ] **Step 6: Verify body and history integrity**

  Recompute body hashes and compare comment counts, linked PRs, acceptance criteria, and architecture references to the baseline.

## Self-Review Result

- All ten migration conditions are assigned to explicit tasks and checks.
- Existing issue content is never edited by the plan.
- Existing parent and dependency relationships are preserved by snapshot/subset checks.
- Activation has a mandatory human stop.
- Rollback is additive/reversible and retains audit history.
- Product implementation, deployment, and issue execution are outside this migration plan.
