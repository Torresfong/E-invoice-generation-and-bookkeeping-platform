# AGENTS.md — Shared AI Project Instructions

This file is the single entry point for Codex and Claude Code. Keep it concise; load linked documents only when the task needs them. Do not treat the absence of a document in context as permission to ignore its rules.

## Authority and task setup
- Follow the current GitHub issue and its acceptance criteria. Read `docs/product/pilot-spec.md`, relevant architecture decisions in `docs/architecture/Decision/pilot-decisions.md`, and relevant code before editing. Cite applicable D-numbers. If instructions conflict, stop and ask.
- For detailed project architecture, domain rules, commands, and conventions, read the relevant sections of `docs/agents/project-context.md`. This is the preserved project guidance formerly in `CLAUDE.md`.
- For development and handoff, follow `docs/agents/ai-workflow.md`. For independent PR review, follow `docs/agents/code-review.md`.
- Other existing sources remain authoritative: `docs/architecture/tenancy.md`, `docs/agents/issue-tracker.md`, `docs/agents/domain.md`, and the contextual glossaries.
- Use the active Wayfinder map #160 and `docs/agents/issue-tracker.md` for ticket selection. A ticket is in the live Frontier only when all five conditions hold: it is open, has no open child, every native blocker is closed, it has no assignee, and it passes its Wayfinder type-specific execution-qualification check. Frontier membership never claims or starts work; assignment is the only claim. GitHub issues #19 and #23 remain unavailable until Torres closes their dedicated native approval blockers.

## Non-negotiable engineering rules
- This is a Malaysian pilot accounting SaaS with client and firm portals, Django and PostgreSQL. Respect the fixed pilot scope and existing architectural decisions.
- Every tenant business table must carry a company ID, enforce PostgreSQL RLS, and use the existing audit mechanisms. Never rely on app-side filtering alone; prove cross-company isolation in tests. Firm administrators cannot access client data.
- Keep external integrations behind existing ports; use fakes in tests. Never add real client documents or signing keys to the repository, logs, or test fixtures.
- Money uses Decimal/NUMERIC, never floats. Preserve gapless invoice numbering, approval locking, document state transitions, and export rules.
- Preserve firm/client role boundaries and no-bypass membership rules. 2FA is a required go-live gate, but its current absence alone is not a development PR merge blocker; document related session/credential risks.
- Respect worker company context from the job row, not payload, and payload identifier-only requirements.
- Test external behavior first, then implement the smallest relevant change. Run the full test suite before claiming completion, including `python manage.py check`, migration drift check, and `pytest` from `backend/`; each acceptance criterion must be met and evidenced. If a required check cannot run, record the exact blocker/result and do not claim completion.
- Never silently change product scope, RLS migrations, production settings, signing keys, or real client data. Stop and ask on those matters.

## Shared test and architecture contract
- Before adding tests, inspect existing coverage and reuse the established HTTP, in-process Jobs, and External Ports seams. Avoid duplicate scenarios, private-method tests, and implementation-coupled mocks; follow red → green with expectations derived from the issue/spec/ADR.
- Existing ADRs outrank agent and skill preferences. Make the smallest change satisfying the issue; do not add speculative abstractions or refactor unrelated code. Never replace a sound abstraction merely for style. Record evidence and seek approval for changes that contradict an ADR.
- Before continuing another agent's task, verify Issue, PR, current HEAD/commits, diff, tests and acceptance criteria against actual repository state. Read handoff notes as pointers, not proof.

## AI collaboration boundaries
- Claude Code and Codex may both implement issues. Use one feature branch per issue and separate worktrees/working directories when working concurrently; never independently push competing changes to the same branch.
- One developer owns the active branch at a time. Handoff through GitHub issue/PR notes and commits, not the prior agent's chat history.
- An explicit instruction from Torres to implement issue #N, including but not limited to `/implement #N`, authorizes the implementing agent to claim a qualified `wayfinder:task`, create its feature branch, implement and verify the issue, commit, push that branch, and open or update a PR against `main` without a separate PR-creation confirmation. Use the repository PR template with `Closes #N`; use a Draft PR and disclose exact blockers when verification or acceptance criteria remain incomplete. This authorization does not extend to merging, deploying, changing production credentials/settings, or crossing a human-decision gate.
- D37: `main` is protected for everyone, including administrators. Changes arrive only by PR; both `backend` and `image` CI checks must pass before merge. No force-push or branch deletion; do not relax protections. Never commit directly to `main`, auto-merge a PR, or claim review approval. Torres decides merges.
- Development self-checks are not the final fresh review. The final reviewer starts in a new Codex task/session, reviews the current PR commit and diff without development chat context, and reports findings without editing code.
- The independent reviewer publishes actionable findings as GitHub PR review comments using a comment-only review. The developer retrieves those comments, fixes them on the same feature branch, pushes the updated HEAD to the same PR, and requests a new fresh-context review. Repeat until the independent reviewer reports no actionable findings; posting comments or resolving findings is not self-certification or merge approval.
- A fresh session is context-independent, but it is not a different model; CI and human merge approval remain essential.
