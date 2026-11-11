# Dual-AI Development and Handoff Workflow

## Assignment and isolation
1. Read the active Wayfinder map #160 and evaluate recursive tickets against all five live Frontier conditions: the issue is open, has no open child, every native blocker is closed, it has no assignee, and it passes the type-specific execution-qualification check in `docs/agents/issue-tracker.md`. Do not route work by `ready-for-agent`; that label is retired. Frontier membership does not authorize or start work.
2. For implementation, select a qualified `wayfinder:task`, read its acceptance criteria, relevant decisions, specs and code, then claim it by assigning the driving developer. Assignment is the only claim. Either Claude Code or Codex can implement. Create `ticket-NN-<slug>` from current `main`; do not push directly to `main`.
3. If working simultaneously on separate tasks, use separate branches and worktrees. Do not let two agents edit the same checkout or push to one branch at the same time.
4. Test first; implement surgically; run the **full** test suite before claiming completion (`python manage.py check`, migration drift check, `pytest` from `backend/`). Every acceptance-criteria checkbox must be satisfied and backed by evidence. If an external prerequisite or environment blocker prevents a required check, use the fake port when appropriate, record the blocked command and reason in the PR, and do not claim completed verification.
5. For tenancy, MyInvois, signing, or money changes, retain any pre-PR independent review required by the project rules. It supplements, and never replaces, the fresh post-PR review loop below.
6. Open a PR against `main` using `.github/pull_request_template.md`. Fill every applicable section, include `Closes #N`, evidence for **every** acceptance criterion, D-number decisions, test commands/results, deployment/migration risks and any missing external validation. Under D37, `main` is protected for administrators too; both `backend` and `image` checks must pass before merge. Do not merge, force-push or delete the protected branch.

## Explicit implementation authorization
When Torres explicitly instructs an agent to implement issue #N—including but not limited to invoking `/implement #N`—that single instruction authorizes the driving developer to complete the normal delivery path for a qualified `wayfinder:task`: assign the issue, create the feature branch, implement and test, commit, push the feature branch, automatically open or update its PR against `main`, and initiate and repeat the required independent-review/update loop. Do not ask for a separate confirmation before pushing the feature branch, creating the PR or requesting each review round.

- Use the existing PR template and put `Closes #N` in the PR body.
- Open a Draft PR when required verification or acceptance criteria are incomplete, or when a disclosed external blocker makes a reviewable partial result appropriate. Record the exact blocked command, result and remaining work; do not claim completion.
- Planning, diagnosis, discussion and review-only requests do not authorize implementation, assignment, branch creation, commits, pushes or PR creation.
- Any code review performed inside the implementing session—including one invoked by `/implement`—is a developer self-check only. It cannot satisfy the independent fresh-context PR review gate.
- The authorization stops at the PR and review-update loop. It never authorizes automatic merge, deployment, production setting/credential changes, real client data use, bypassing CI or branch protection, or crossing a human-decision/ADR gate.

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
1. After the PR is open, record its URL and current HEAD SHA, then dispatch Codex GitHub Code Review as described below. The GitHub reviewer is a **new, separate review task** for the PR; do not continue the implementation chat or pass it the development conversation. Initial CI may run concurrently, and any exact blocker must be disclosed. If Codex review is unavailable, use another genuinely independent fresh-context reviewer only with Torres's approval.
2. The reviewer follows `docs/agents/code-review.md`, reads the exact PR base and current HEAD SHA, diff, issue acceptance criteria and relevant source/ADR/tests, and remains code-read-only. When findings exist, it publishes each specific actionable finding as a GitHub PR review comment rather than approval or self-certification; non-inline limitations and evidence belong in the review summary. When none exist, Codex may publish only its review receipt/summary instead of a `COMMENT` review.
3. The implementing developer retrieves the review receipt/status, reviewed commit SHA, review summaries, inline comments and unresolved threads from the PR, then follows exactly one result branch:
   - **Findings:** address every actionable finding on the **same feature branch**, rerun appropriate checks, commit, and push the updated HEAD to the **same PR**. The developer may explain or challenge a finding with evidence but does not certify its own fix.
   - **Completed with no findings:** accept the round only when the receipt/summary says `status: completed`, its reviewed commit SHA equals the current PR HEAD, and it explicitly reports both no findings and no unresolved findings. Stop waiting for a review comment and proceed to the remaining CI and human gates.
   - **No conclusive receipt:** keep the review gate open. An empty comment list, absent review thread or elapsed time alone is not evidence that review completed or found nothing.
4. For every updated HEAD, confirm that automatic Codex review has queued for that SHA. If it has not, post `@codex review` again. The new fresh-context reviewer verifies prior findings against the actual code and reviews the new diff for regressions. It publishes any remaining or new findings; when none remain, its exact-HEAD completed receipt closes that review round without a separate comment. Repeat steps 3–4 until a fresh review of current HEAD reports no actionable findings, subject to the three-attempt escalation limit below.
5. Track unsuccessful fix attempts separately for each actionable finding. An attempt counts when the developer pushes a change intended to resolve that finding and a fresh review reports that the same underlying defect remains. After the third unsuccessful attempt on one finding, stop the automatic fix/review loop: keep or convert the PR to Draft, publish an escalation note linking the finding and the three reviewed HEAD SHAs, summarize each attempted fix and its test evidence, and ask Torres to inspect and decide the next action. Do not make a fourth attempt, dismiss the finding, broaden scope, merge or deploy without Torres's direction.
6. Torres checks final-HEAD CI (`backend` and `image`), finding comments or the verified no-findings receipt, unresolved exceptions and human gates, then decides whether to merge. Review evidence is not an approval or automatic merge gate.

## GitHub review dispatch
Codex GitHub Code Review must remain connected and enabled for this repository. The repository is configured to run automatic review on every push, but the initial branch push normally precedes PR creation, so the implementing agent must dispatch the first review from the open PR:

1. Record the PR number and current HEAD SHA.
2. Post a PR conversation comment containing exactly `@codex review`, unless GitHub already shows a Codex review queued for that exact SHA.
3. Confirm the eyes reaction or other GitHub indication that the review is queued or in progress. Posting the comment alone is not proof that review started.
4. After each later push, confirm automatic review queues for the new HEAD SHA; if it does not, post `@codex review` again.
5. If review cannot be dispatched or the GitHub connection is unavailable, keep or convert the PR to Draft, disclose the blocker, and do not claim the independent review gate is complete.

The prompt and shared documents guide the reviewer; they do not technically enforce code-read-only behavior, branch protection or the human merge decision.
