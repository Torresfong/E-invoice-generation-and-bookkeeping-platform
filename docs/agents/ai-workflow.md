# Dual-AI Development and Handoff Workflow

## Assignment and isolation
1. Select an unblocked `ready-for-agent` issue, except #19/#23 without permission; read issue, acceptance criteria, relevant decisions, specs and code.
2. Either Claude Code or Codex can be the implementing developer. Create `ticket-NN-<slug>` from current `main`; do not push directly to `main`.
3. If working simultaneously on separate tasks, use separate branches and worktrees. Do not let two agents edit the same checkout or push to one branch at the same time.
4. Test first; implement surgically; run relevant checks and the full suite when feasible. Missing external credentials: use the port fake and disclose limitations.
5. For tenancy, MyInvois, signing, or money changes, retain the pre-PR independent review required by the project rules.
6. Open a PR against `main` with `Closes #N`, acceptance-criteria evidence, D-number decisions, test commands/results, deployment/migration risks and any missing external validation. Do not merge.

## Shared TDD test boundaries
Claude Code using Matt Pocock's TDD skill and Codex using its own TDD workflow **must follow the same project-defined test seams**. Tool- or skill-specific preferences do not override the repository's testing contract:
1. Prefer HTTP-level behavior tests and in-process job-runner tests with all four external ports faked; test denied cross-company access.
2. Use real MyInvois sandbox contract tests only when credentials are available; otherwise skip cleanly and report the gap.
3. Use the extraction regression harness with firm samples stored outside the repository.

Both developers write a failing behavior test before the minimal implementation and verify red → green. Do not introduce private-method tests, internal-mock coupling, or a new test seam merely because a skill suggests it. The seams above are already agreed; ask Torres only when a requirement needs a materially new or changed test boundary. Relevant ADRs and acceptance criteria determine expected behavior, not the implementation itself.

## Test coverage and architecture consistency
- Before writing a new test, search the existing test suite for the same externally observable scenario. Extend or reuse an existing test where appropriate; add a distinct test only for a new behavior, failure mode, security boundary or meaningful regression.
- Derive expected results from acceptance criteria, worked financial examples and ADRs, not from the implementation. Preserve deliberate cross-company negative tests even if similar-looking positive tests exist.
- Existing ADRs and documented domain patterns take precedence over Claude Code or Codex skill preferences. Implement only the current issue; no speculative abstractions, unrelated refactors, or replacing a sound service merely because another agent prefers a different style.
- If a proposed architecture change is necessary, record the defect, affected ADR, migration/compatibility consequences and tests; stop for Torres's approval if it contradicts an ADR or changes scope.

## Token-limited handoff
When switching from Claude Code to Codex or the reverse, update the issue or PR with a short factual handoff:
- Issue, branch, and current HEAD commit SHA
- Completed changes and outstanding acceptance criteria
- Decisions and relevant ADR/D-numbers (links, not copied documents)
- Commands/tests run with results; unavailable credentials/services
- Known bugs, risks, migrations and next action

The incoming developer must independently inspect current Git diff/status, code and issue. A handoff is navigation context, not proof that changes are correct.

## Fresh PR review gate
1. After implementation and CI, start a **new, separate Codex review task** for the PR. Do not continue the implementation chat.
2. Reviewer uses `docs/agents/code-review.md`; reads the PR base/head/diff, issue acceptance criteria and relevant source/ADR/tests. Reviewer is read-only and does not fix findings or merge.
3. Developer addresses actionable findings on the feature branch and reruns checks. After new commits, request another fresh review of the updated HEAD.
4. Torres checks CI (`backend` and `image`), review findings and exceptions, then decides whether to merge. A reviewer saying “no findings” is not an automatic approval gate.

## Optional GitHub integration
Codex GitHub code review must be enabled for the repository separately. If available, request it from the PR conversation with `@codex review`. The prompt and shared documents guide the reviewer; they do not technically enforce read-only permissions or branch protections.
