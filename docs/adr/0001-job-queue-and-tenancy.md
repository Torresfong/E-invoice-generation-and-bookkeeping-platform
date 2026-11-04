# 0001: Jobs carry a company id and sit under row-level security

Status: accepted by the repository owner on 2026-10-28, after an independent review of the first draft. Relates to D10 (Postgres-backed queue), D11 (row-level security) and D36.

This replaces the first draft, which exempted the job table from row-level security as a "global system table". The review showed the exemption was weaker than argued, so it is dropped.

## Context

Background work (extraction, MyInvois polling, reminders, exports) is queued in a `Job` table (D10), and jobs refer to tenant records. A worker has to find pending jobs across all companies. D11 says every business table carries a company id and Postgres row-level security enforces isolation from the first migration.

## Decision

1. **Jobs are tenant-scoped.** `Job` gets a `company_id` column (null only for system jobs that touch no tenant data) and a row-level security policy for the web role, with a check on insert. A request from one company can neither see nor queue jobs for another.
2. **Two database roles.** The web role is subject to the policy. The worker role may claim rows across companies (with `SKIP LOCKED`) but is neither the table owner nor a superuser, so row-level security is never skipped silently.
3. **Context comes from the column, never the payload.** The company id is a real column set from the authenticated session when the job is queued. Before calling a handler, the worker sets the tenant context from that column. A handler therefore cannot read another company's rows even if its payload names one.
4. **Identifiers only in payloads.** A payload holds ids and short references, never document content, names, TINs or amounts. `enqueue()` enforces this: it accepts only int, bool, None, UUID and strings of at most 40 characters, rejects everything else (including floats, decimals, lists and objects), and names the offending key in the error without echoing the value.
5. **No content in errors or logs.** A failed job stores only the exception class name, and the log line carries the job id, name and class. Exception messages and tracebacks are neither stored nor logged by the queue. Handlers must follow the same rule when they log.
6. **No exposure.** The job table is not shown in any admin screen, API or report, and a test will pin that admin stays uninstalled or excludes it.

## What exists now and what comes later

| Rule | State |
|---|---|
| 4 identifiers only | Built in ticket 01 and tested |
| 5 no content in errors or logs | Built in ticket 01 and tested |
| 1 to 3 company id, row-level security, role split | Built in ticket 02d (#34) and tested: the `worker` role, the company taken from the job's column, a payload naming another company's record reads nothing |
| Lease, retry, backoff, idempotent external calls | Built in ticket 24 (#27) and tested: `docs/architecture/tenancy.md`, "Job execution" |
| Purge of finished jobs | Built in ticket 24 (#27) and tested: the daily `purge_finished_jobs` system job |

Since ticket 02d the table has a company id and row-level security, and handlers run as the `worker` role with the company context taken from the job's column. A handler touches tenant data only through that context. A system job (no company) sees none. Details and limits: `docs/architecture/tenancy.md`, "Background jobs".

## Why not leave the queue global

The first draft argued that a worker must see every tenant's jobs, so row-level security would need a bypass anyway. The review's answer is that a worker's cross-tenant visibility is a privilege of the worker role, not a reason to expose the table to the web role. Left global, any bug in one tenant's request could list other tenants' record ids or queue work against another company.

## Consequences

- Debugging a failed job means reproducing it from the ids, not reading a stored message.
- Rule 4 limits payloads, but the payload check cannot tell an id from a short secret. Review must still check every new job.
- Production alerting on the count of failed jobs is needed, since the stored error is deliberately thin.
- A person with direct database access can read job ids and names but no tenant content.
