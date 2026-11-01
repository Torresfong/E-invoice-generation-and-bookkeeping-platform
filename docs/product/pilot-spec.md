# Pilot spec: e-invoicing and bookkeeping document platform

Status: ready for agent. Source: the grilling session of 2026-10-27, building on the MVP pilot proposal of 2026-10-09.

## Problem Statement

Small Malaysian companies must issue LHDN MyInvois e-invoices, and they still send bookkeeping documents (sales and purchase invoices, receipts, bank statements, payroll and loan papers) to their accounting firm by email. Pilot clients have no simple way to issue compliant e-invoices, and the firm loses time chasing documents, sorting an email inbox, and keying every field by hand into SQL Account. Blurry or incomplete documents are discovered days late, missing documents are chased manually, and nobody can see at a glance what has been issued, uploaded or is still outstanding.

## Solution

One platform with two portals. Clients draft, approve and submit LHDN e-invoices and upload bookkeeping documents. The firm receives every document per client and per month, has it classified and read automatically, corrects the extracted fields in a review queue, chases missing documents on a checklist, and exports one verified monthly batch per client for import into SQL Account. Our company acts as the MyInvois intermediary. The pilot runs with 3 to 4 real clients (5 shortlisted), against MyInvois production, built by two developers who start from an empty repository.

## User Stories

### Client owner: e-invoicing
1. As a client owner, I want to keep my buyers' details once in a customer master, so that I never retype them on an invoice.
2. As a client owner, I want to draft an invoice by form, so that I can issue invoices without any assistant.
3. As a client owner, I want to describe an invoice in plain words to an assistant, so that the draft is filled from my customer master and I am asked only for missing mandatory fields.
4. As a client owner, I want to issue invoices, credit notes, debit notes and refund notes to local businesses, so that I can cover normal sales corrections.
5. As a client owner, I want to be the only person who can approve and submit an invoice, so that nothing reaches LHDN without my decision.
6. As a client owner, I want every approval recorded with who and when, so that there is a trustworthy audit trail.
7. As a client owner, I want to see how many invoices are waiting for my approval on my dashboard and by email reminder, so that approvals do not stall when I am busy.
8. As a client owner, I want live MyInvois status on every invoice (draft, waiting approval, submitted, validated, invalid, cancelled, with a "rejection requested by buyer" marker on a validated invoice), so that I know where each stands.
9. As a client owner, I want a clear explanation when LHDN marks an invoice invalid, and to correct and resubmit it, so that a validation failure does not cost me a day of work.
10. As a client owner, I want to be notified when a buyer requests rejection, so that I can respond within the permitted window; the invoice stays validated until I cancel it.
11. As a client owner, I want to cancel a validated invoice within LHDN's window, so that mistakes can be reversed correctly.
12. As a client owner, I want each validated invoice emailed to my customer as an English PDF with the MyInvois QR code and validation link, so that the customer gets a proper document.
13. As a client owner, I want the email to come from the platform's domain under my company's name with my address as reply-to, so that replies reach me.
14. As a client owner, I want to choose the tax type and rate for each line and have the system calculate the amounts, so that SST is correct and still my decision.
15. As a client owner, I want to invoice in a foreign currency with the daily reference rate proposed and overridable, so that I follow the rate I actually used while the original currency and amount are always kept.
16. As a client owner, I want my invoice numbers to continue from my existing books, with my own prefix, gapless per document type, so that numbers never duplicate or skip.
17. As a client owner, I want the platform to be my only invoice issuer after go-live, so that two systems never produce the same number.
18. As a client owner, I want an issued invoice to keep the buyer details it was issued with, so that later edits to the customer never change a past invoice.
19. As a client owner, I want the dashboard to show all invoices, e-invoice status, uploaded documents and outstanding requests in one place, so that I need no other tool to see where I stand.
20. As a client owner, I want to see and approve changes to a customer's TIN, legal name or registration number, so that no one else changes who my buyer legally is.
21. As a client owner, I want the interface in English or Chinese, so that I can work in my language.
22. As a client owner, I want to authorise the intermediary in MyInvois and sign a service agreement, so that onboarding is clear and lawful.
23. As a client owner, I want to consent to AI processing and cross-border transfer in plain wording, so that I know where my documents go.
24. As a client owner, I want a full export of my data if the pilot ends, so that I am never locked in.

### Client staff
25. As client staff, I want to draft invoices and upload documents, so that the owner only has to approve.
26. As client staff, I want to confirm the creation of a new customer proposed by the assistant, so that routine setup does not wait on the owner.
27. As client staff, I want to be unable to approve or submit invoices, so that responsibilities stay clear.
28. As client staff, I want an upload that is blurry, cropped or incomplete bounced back within seconds with a reason, so that I can re-take the photo at once.
29. As client staff, I want to upload a digital PDF, a scan or a phone photo, in English, Chinese, Malay or mixed, so that I never convert documents first.
30. As client staff, I want to see which documents the firm is still waiting for, so that I know what to send.
31. As client staff, I want reminder emails on day 3, 7 and 14 after the due date, so that I do not forget, without being nagged forever.

### Assistant
32. As a user, I want the assistant to search my company's customers, so that it fills the draft from real records.
33. As a user, I want the assistant to propose a new customer or an amendment to an existing one, shown as before-and-after values, so that I confirm exactly what will change.
34. As a user, I want the TIN of a proposed customer checked with MyInvois before I can confirm, so that wrong tax identities are caught early.
35. As a user, I want the assistant to be unable to delete customers, approve or submit invoices, or choose an uncertain tax, so that the highest-risk actions stay with people.
36. As a user, I want the assistant to ask me for missing mandatory fields rather than guess, so that drafts are complete.
37. As the firm, I want every assistant action logged with the user who triggered it, so that AI-assisted changes are auditable.
38. As a client owner, I want text inside uploaded documents never to be able to instruct the assistant or extractor, so that a crafted document cannot alter my data.

### Firm accountant
39. As an accountant, I want all clients' documents in one place organised per client and per month, so that the email inbox is no longer the system of record.
40. As an accountant, I want every upload classified automatically, so that I do not sort by hand.
41. As an accountant, I want supplier, date, amounts, tax and line items extracted from sales invoices, purchase invoices and receipts, so that I do not key them.
42. As an accountant, I want a review queue showing the extracted fields beside the document image, so that I can check and correct quickly.
43. As an accountant, I want to mark a document verified, so that only checked documents are exported.
44. As an accountant, I want to send an unreadable document back with a reason, so that the client re-uploads promptly.
45. As an accountant, I want to set each client's monthly checklist of expected documents, so that the system chases what is missing.
46. As an accountant, I want to request any one-off document and have the system follow up until it arrives, so that I do not chase by hand.
47. As an accountant, I want reminders to stop after three and flag the client for a phone call, so that follow-up escalates to me.
48. As an accountant, I want to pause reminders for a client, so that I control the tone of the relationship.
49. As an accountant, I want one monthly export per client of platform-issued sales invoices, old sales invoices uploaded from before the client adopted the platform, and verified purchase invoices and receipts, with a source column telling them apart, so that I import once by hand (D41, D42, D43).
50. As an accountant, I want no document exported twice, and documents locked as exported only when I confirm my import worked, so that SQL Account never gets duplicates and a failed import can be redone (D43).
51. As an accountant, I want exported documents locked, and later cancellations or corrections to appear as new items in the next batch with a "changed after export" flag, so that nothing is silently altered after import.
52. As an accountant, I want the export to carry extracted fields with empty coding columns, so that I code in SQL Account as today.
53. As an accountant, I want bank statements, payment vouchers, payroll and loan documents stored and searchable, so that I can find them without email.
54. As an accountant, I want original currency and amount stored on foreign purchase documents, so that I apply the rate in SQL Account.
55. As a backup accountant, I want to use the same review queue when the lead is away, so that the pilot does not stall.
56. As the firm lead, I want weekly time for the pilot commitment written down, so that capacity matches the targets.

### Platform operator (our team)
57. As an operator, I want each client company's data isolated by the database itself, so that a coding mistake cannot leak data across companies.
58. As an operator, I want a full audit trail of who drafted, approved, submitted, corrected and exported, so that every outcome can be explained.
59. As an operator, I want the signing key held in a key vault with every signing logged and separate sandbox and production certificates, so that a leak cannot happen from a laptop or a repository.
60. As an operator, I want MyInvois submissions queued with an owner-visible status banner when LHDN is down, so that failures are never silent.
61. As an operator, I want database and files stored in Malaysia with daily backups copied to a second Malaysian zone, so that the residency promise holds.
62. As an operator, I want records kept at least 7 years under a retention agreement, so that tax record rules are met.
63. As an operator, I want a support channel with business hours, a one-working-day target and a weekly rotation between the two developers, so that pilot clients are never unsupported.
64. As an operator, I want all MyInvois calls behind one module, so that a library or gateway can replace it if the sandbox spike fails.
65. As an operator, I want the AI provider swappable behind one port, so that the provider chosen from the sample test can change without rework.

## Implementation Decisions

**Stack.** Django with server-rendered pages, HTMX for interaction, and Alpine.js for small client-side behaviour. Postgres 16. A job queue backed by Postgres, with no separate broker. S3-compatible object storage and managed Postgres in a Malaysian cloud region (AWS Malaysia is the working choice; availability of the needed services is to be confirmed in week 1). Docker Compose for local development. This replaces the previously scaffolded FastAPI and Nuxt, chosen because both developers know Python and Postgres but not Vue.

**Tenancy.** Every business table carries a company identifier. Isolation is enforced by Postgres row-level security from the first migration, not only in application code. The database is designed so that per-accountant assignment, a firm administrator role and one login across several companies can be added later.

**Roles.** Exactly three: client owner, client staff, firm accountant. Only the owner approves and submits invoices; there is no delegation. A company has one owner login. Staff may confirm creating a customer; only the owner may change a customer's TIN, legal name or registration number. Several accountants may use the firm portal from day one, with one `CompanyAccess` row per pilot company and no bypass role, and every read of a company's data audited (D40, D45). Staff and the assistant may propose a change to those identity fields; the owner approves, and every customer write validates the TIN with MyInvois (D46).

**External ports.** Four boundaries, each a single module with a fake for tests: MyInvois gateway (login on behalf of taxpayers as intermediary, TIN validation, document signing, submission, status polling, cancellation, rejection handling), AI provider, email, object storage.

**MyInvois.** Direct submission to LHDN, JSON document format, signed with the intermediary's certificate. Invoice states: draft, waiting approval, submitted, validated, invalid, cancelled. A buyer's rejection request is a marker on a validated invoice, not a state: LHDN shows it as "Requested for Rejection" and keeps the invoice valid until the owner cancels it inside LHDN's window (72 hours in LHDN's current guideline; confirm in the sandbox spike, D14). Delivery to the buyer (emailed, downloaded, failed) is its own attribute and never an invoice state. Invalid invoices can be corrected and resubmitted. Submissions are queued if LHDN is unavailable and the owner sees a status banner. A two-week sandbox spike (login, sign, submit, poll, cancel) comes first; if no accepted sandbox submission exists by week 3, it is escalated as the main risk.

**Signing key custody.** The key lives in a managed key vault or secrets service, readable only by the signing module, never in the repository or the environment file, every signing operation logged. Sandbox and production use different certificates. The vault choice depends on the certificate type LHDN issues.

**Numbering.** One gapless sequence per company and document type, with owner-set prefix and starting number continuing from their existing books. The platform is the only issuer from a client's go-live date. The owner confirms the last used number and sets the go-live date at onboarding. A draft has no number; the number is allocated when the owner approves and submits in one action, a cancelled invoice keeps its number, and a corrected invalid invoice is resubmitted under the same number if LHDN accepts that (D44).

**Buyer snapshot.** Buyer details are copied onto an invoice at approval and locked. Customer changes apply only to new drafts. Customer change history goes to the audit trail.

**Tax and currency.** The owner chooses tax type and rate per line; the system calculates. For foreign currency the system proposes the daily reference rate (Bank Negara Malaysia daily reference rate unless the firm's SQL Account uses another source, to be asked in week 1); the owner can override. Original currency and amount are never overwritten.

**Invoice PDF and email.** English PDF with the MyInvois validation QR code and link, generated server-side. Email is sent from the platform's own domain, with the client's name as sender and the client's address as reply-to; SPF and DKIM are set up in weeks 1 to 2.

**Assistant.** The model has no database access. The backend calls the provider, sends only the minimum data needed (for example, matching customers rather than the whole master), and executes any requested function using the signed-in user's permissions and row-level security. Exactly four functions: search customers, propose create customer, propose amend customer, save draft. Proposals are written only after a human confirms on a before-and-after screen, and the TIN is validated with MyInvois before confirmation is enabled. There is no delete function and no submit function, and the assistant never chooses an uncertain tax. Text read from uploaded documents is treated as data only, and extraction output must pass schema validation before saving. Every assistant action is audit logged. This is an accepted scope increase over the original proposal, which excluded assistant-created customers.

**Document pipeline.** Fixed states: uploaded, quality-checked, classified, extracted, in review, verified, exported; side states: returned for re-upload and rejected. The blurry or cropped check runs during the upload using a fast local image check (sharpness, edges, page found) plus a basic readability signal from the extractor, so the bounce is visible within seconds. Only verified documents can be exported. An uploaded sales invoice dated on or after the company's go-live date is returned with the reason "issue it in the platform"; older ones follow the same pipeline as purchase invoices (D41).

**Extraction.** A vision-capable model returning structured output, with no separate OCR engine at first. The provider is chosen in the weeks 1 to 2 sample-document test. In-region processing is preferred but not mandatory; client consent and a no-training data processing agreement are the baseline, and the cross-border transfer is named in the consent text and service agreement.

**Checklist and reminders.** The accountant sets a monthly checklist per client, plus one-off requests. Reminders go out on day 3, day 7 and day 14 after the due date, then stop and flag the client to the accountant. The accountant can pause reminders per client.

**SQL Account export.** One monthly batch per client and document-date month (D42): platform-issued sales invoices, verified old sales invoices uploaded from before the client's go-live date, and verified purchase invoices and receipts, with empty coding columns and a source column (platform-issued or uploaded) (D41). The accountant imports the file into SQL Account by hand; the file can be downloaded repeatedly, and documents are locked as exported only when the accountant confirms the import worked. An unconfirmed batch can be regenerated or voided (D43). No document is exported twice. Exported documents are locked; later cancellations, credit notes or corrections appear as new items in the next batch with a "changed after export" flag. The export format is designed only after the firm has hand-built a file that its own SQL Account accepted; the firm names an owner for that test by Nov 6, 2026.

**Retention and exit.** All invoices, documents and audit logs kept at least 7 years under a written retention agreement. Each client can receive a full export (PDFs and a data file) if the pilot ends; this exit export is a small deliverable before Gate 3.

**Languages.** Interface in English and Chinese for client portal, built continuously; firm portal in English. Documents in English, Chinese, Malay or mixed.

**Hosting and operations.** Database and files in Malaysia, daily backups copied to a second Malaysian zone. Separate staging (MyInvois sandbox) and production (MyInvois production) environments. Support: business hours, one-working-day response target, one developer on call per week, with a named channel per client.

**Delivery order.** Vertical slices: (1) tenancy, authentication and audit skeleton; (2) complete MyInvois invoice flow including PDF and email, demoed to the firm by week 6; (3) upload pipeline with extraction; (4) review queue and SQL export; (5) checklists and reminders; (6) SST and foreign currency woven into the invoice flow; (7) customer proposal screens with TIN validation (weeks 7 to 9), then the assistant last. Nothing is cut from scope.

**Calendar.** Week 1 starts 2026-10-27. Target gates: Gate 1 on 2026-11-09, Gate 2 on 2027-01-25, Gate 3 on 2027-02-22. The schedule may slip up to an outer limit of about 2027-04-27. Gate 3 moves day for day with the signing certificate and intermediary approval and is never declared on sandbox results. If Gate 2 slips past week 18, stop and review before hardening. Chinese New Year (2027-02-06 to 02-08) falls inside hardening; the first client should go live the week of 2027-02-01 where possible.

## Testing Decisions

A good test checks external behaviour only: what a signed-in role sees and can do, what is sent to the external ports, and what ends up exported. It does not assert on internal structure, private functions or query shapes.

**Seam 1 (main).** The web application's HTTP interface with the background job runner executed in-process. Tests sign in as a role, call the real pages and endpoints, run queued jobs, and assert on responses, stored state and calls made to the ports. The four ports (MyInvois gateway, AI provider, email, object storage) are replaced by fakes. Company isolation is covered here by end-to-end cross-company requests that must fail. Cases include the invoice lifecycle, numbering, buyer snapshot, role limits, document states, bounce-on-upload, review and verification, reminders and escalation, export idempotency and post-export changes, assistant function limits and human-confirmed writes, and prompt-injection attempts through uploaded documents.

**Seam 2.** Contract tests of the MyInvois port against the real sandbox, proving the fake matches LHDN behaviour. This is also the evidence for the week 1 to 3 spike.

**Seam 3.** An extraction regression harness on the AI port: the 50 to 100 real sample documents, scored per document type on every change to the prompt or provider. The set needs sign-off from the firm's lead accountant and is the evidence for the 80% target.

CI on GitHub Actions blocks merges when any of these fail. Prior art: none; the repository is greenfield and its CI is a placeholder, so the first slice must establish the test harness and replace the placeholder.

## Out of Scope

- Automatic extraction of bank statements, payment vouchers, payroll records and loan documents (stored and searchable only).
- Account coding inside the platform; coding columns are exported empty.
- Consolidated e-invoices for consumer sales and self-billed e-invoices for foreign suppliers.
- SST returns and automatic tax-rate determination.
- The platform's own ledger and financial reports; SQL Account stays the books of record.
- Payment and receipt tracking against invoices.
- Automatic detection of missing documents from bank statement gaps.
- Assistant deleting customers, approving or submitting invoices, deciding uncertain tax, or answering general accounting questions.
- Delivery by WhatsApp or WeChat; Malay interface and bilingual PDFs.
- Per-accountant client assignment, firm administrator role, one login across several companies, and approval delegation.
- Foreign-currency conversion on uploaded purchase documents.
- Offline use and a mobile app.

## Further Notes

Quality targets (agreed, with definitions): every pilot client has an e-invoice accepted by MyInvois production; the firm has imported one full month of exports into SQL Account; 95% or more of e-invoices accepted on first submission, counting real client invoices only; 80% or more of sales invoices, purchase invoices and receipts needing no field correction, measured per document type on actual uploads including phone photos; accountant time per document at least halved against a baseline of at least 40 documents timed by week 8 and re-timed on the same set. A miss on one target is reported as a partial result with the cause. Each pilot owner states the monthly price they would pay at go-live and again at the end.

Pilot clients: shortlist 5, aim to sign 3 to 4, with a named owner contact for each by 2026-11-09. Criteria: local business-to-business sales only, no foreign supplier self-billing, existing SQL Account bookkeeping client, owner willing to approve personally. The product owner reports (2026-11-01) that the pilot clients are under RM3m annual turnover. LHDN's current guideline exempts taxpayers under RM3m unless a holding company, related company or non-individual shareholder has RM3m or more, so whether each client must e-invoice is open (see the open items and D29).

The firm commits a lead accountant and a named backup, with weekly hours in writing. A Malaysian lawyer reviews the service agreement, the AI and cross-border consent text and the AI data processing agreement by week 12; no client signs before that.

Still open and to be settled by dated work, not assumed: the AI provider (sample test, weeks 1 to 2); the exchange-rate source the firm uses; the SQL Account version and file format (the import method is manual, D43); the price after the pilot (signal only); a paid review session with someone who has integrated MyInvois, before Gate 1. Two assumptions are carried without confirmation: our company is a registered Malaysian company LHDN will accept as an intermediary, and the AWS Malaysia region offers the needed managed services.

Housekeeping before the first commit: add the environment file to the ignore list, add Dockerfiles, and replace the placeholder CI.
