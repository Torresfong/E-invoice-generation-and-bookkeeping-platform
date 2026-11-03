# Independent Codex Fresh PR Review

## Independence
- Start a new Codex task/session for every final review round; do not reuse the implementing developer's conversation or conclusions.
- Review the exact PR base and current HEAD commit, issue/spec and relevant code. Do not assume handoff notes are correct.
- Review-only: no code edits, pushes, GitHub approvals, merges, or destructive actions. Report evidence and actionable findings.
- If the implementation was written by Codex, the fresh review reduces shared-context bias but does not provide cross-model independence.

## Check in priority order
1. Tenant isolation: RLS on new business tables, company-scoped access, firm/accountant boundaries, adversarial cross-company tests.
2. Auth, authorization and sessions: role escalation, SQL-as-web credential/session takeover, leakage and audit trails. 2FA remains a **pre-go-live** requirement, not by itself a development merge blocker.
3. Financial correctness: Decimal/NUMERIC, gapless per-company/type numbering, transactions, approval/locking, document state and export integrity.
4. MyInvois/signing and data protection: sandbox versus production credentials, no secret/client content in code/logs/jobs, external ports and fakes.
5. Regressions and operations: migration/RLS effects, idempotency, worker tenant context, API behavior, translations, missing tests, CI, compatibility with D-number decisions.

## Review report
- List findings first, ranked `Critical`, `High`, `Medium`, `Low`, with file/path and line, trigger scenario, consequence, and smallest safe fix.
- Distinguish verified bug from plausible risk or missing evidence; do not report stylistic nits as bugs.
- Then state test/CI evidence inspected, unresolved limitations, and any relevant go-live-only risks.
- If no actionable findings, say so explicitly but do not imply the PR is guaranteed safe or approved.
- Any fixes belong to a developer task, followed by new CI evidence and a new fresh review on updated HEAD.
