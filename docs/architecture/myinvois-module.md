# Invoice engine and MyInvois module

Decisions: D59 (1), D61 (3), D61 (4). Ticket: #128. This note says which side of the boundary each later ticket's code goes on. It adds no decision; it applies D59 and D61.

## The rule

- **`core` is the invoice engine.** It owns drafting, numbering, approval and locking, PDF and email, and the SQL Account export.
- **`backend/myinvois/` is a separate Django app** that owns everything LHDN-specific. It reads `core`; **`core` imports nothing from `myinvois`**, not even in a function body or through `importlib`.
- The engine reaches the MyInvois side by only these doors, all of which live in `core`:
  1. the **gateway port** (`core.ports`, `ports.get().gateway`);
  2. the **job runner** (`core.jobs`): the engine enqueues a job by name with an identifier-only payload; the MyInvois app registers the handler with `core.jobs.register` from a `myinvois/jobs.py` that its `AppConfig.ready()` will import (planned: no handler exists yet, the first comes with #47). Company context comes from the job row (D36, D40);
  3. **one question**, `core.einvoicing.einvoicing_enabled(company, on_date)` (#119): is e-invoicing on for this company on this date? The setting is one table in `core`; the MyInvois app reads it, never the reverse.
- **The "engine-facing interface" is therefore not a module inside `myinvois`.** Under D61 (4) the engine imports nothing from `myinvois`, so the interface is the three doors above, all in `core`: the gateway port, the job runner and `einvoicing_enabled`. `myinvois` exposes nothing to the engine by import; the boundary test enforces that "nothing else".
- The MyInvois app only reads the invoice, the buyer snapshot and the company's e-invoice setting. It does not reach into other tables.
- `myinvois/tests/test_module_boundary.py` enforces the import direction on every file under `core/` (tests included, migrations excluded), by static and dynamic import.

Because the engine cannot import the MyInvois app, an engine feature that needs something from it takes it through a door above (a job, or a read-only protocol defined in `core.ports` that the app implements), never by an import.

## Where later code goes

| Code | Ticket | Side |
|---|---|---|
| Draft form, customer picker | #44 | `core` |
| Gapless numbering | #45 | `core` |
| Owner approval, buyer snapshot, lock | #46 | `core` |
| Invoice PDF, email | #12 | `core` (PDF, email); `myinvois` (validation link and QR code, which need LHDN's `uuid` and `longId`) |
| SQL Account export | #60 to #63 | `core` |
| LHDN code lists and required-field rules for the document | #122 | `myinvois` |
| Mapping an invoice to LHDN's JSON document | #123 to #125 | `myinvois` |
| Signing | #43 | `myinvois` (the signer port stays in `core.ports`) |
| Submission, and its retry and recovery rules | #47 | `myinvois` (`submission.py`, moved here from `core` by #128) |
| Polling status | #48 | `myinvois` |
| Cancellation inside the LHDN window | #50 | `myinvois` |
| Buyer rejection request | #51 | `myinvois` reads it; the owner's notice goes through the engine's email port |
| Representation checks (D57) | #23 | `myinvois` |
| Company e-invoice setting model and `einvoicing_enabled` (exists in `core/einvoicing.py`) | #119 | `core` |
| Item and customer master (hold LHDN codes as plain data) | #7, #121 | `core` |

The item and customer masters and the LHDN code-list loader that items use today (`core/lhdn_codes.py`) stay in `core`: they are master data the engine edits. Rules that interpret a code (#122) go to `myinvois`.

## Where an invoice's LHDN fields are stored

An invoice's LHDN fields (`uuid`, `longId`, status, validated time) belong to the MyInvois side, so a normal invoice carries none of them (D59 (3)).

**Recorded for #47, to confirm there:** one table owned by the `myinvois` app, one row per submitted invoice, keyed by the invoice's id (a foreign key by id only, no ORM import of the engine's model into the engine), carrying the company id under the same RLS pattern and audit as every tenant table. The first such migration comes with #47 and needs Torres's RLS review; #128 adds no table. The SQL Account export (#60 to #63, D51) gets the values it needs through a read-only protocol defined in `core.ports` and implemented by the `myinvois` app, so the engine still imports nothing from it.

## What #128 moved

`core/submission.py` became `myinvois/submission.py` and its tests became `myinvois/tests/test_submission.py`, with no behavior change. The gateway port, the fake and the signing port stay in `core/ports/`.
