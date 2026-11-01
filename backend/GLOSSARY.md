# Backend

The platform where Malaysian companies issue LHDN MyInvois e-invoices and upload bookkeeping documents, and the accounting firm reviews them and exports a monthly batch to SQL Account.

## Language

**Company**:
A tenant: one business that uses the platform, whose data is isolated from every other company.
_Avoid_: Client company, account, tenant

**Customer**:
A record in a company's customer master describing someone it sells to.
_Avoid_: Client, buyer, debtor

**Buyer**:
The customer details copied onto an invoice at approval and locked.
_Avoid_: Customer (when speaking of an issued invoice)

**Invoice**:
An e-invoice the platform issues to LHDN for a company, including credit, debit and refund notes.
_Avoid_: Bill, document

**Document**:
A bookkeeping file a company uploads, such as a purchase invoice, receipt or bank statement.
_Avoid_: Invoice (for uploads), file

**Go-live date**:
The date from which the platform is a company's only invoice issuer; uploaded sales invoices dated on or after it are returned.
_Avoid_: Start date, launch date

**Batch**:
One export of a company's verified items for one document-date month, imported into SQL Account by hand.
_Avoid_: Export file, run

**Exported**:
The state of a document once the accountant has confirmed that its batch was imported; it is then locked.
_Avoid_: Downloaded, sent

## People

**Client owner**:
The one login per company who approves and submits invoices.
_Avoid_: Admin, client

**Client staff**:
A company user who drafts invoices and uploads documents but cannot approve or submit.
_Avoid_: Employee, member

**Firm**:
An accounting firm. Every company belongs to exactly one firm, and a firm's accountants reach only that firm's companies. Created only by the platform operator.
_Avoid_: Practice, agency, accounting company

**Firm accountant**:
A user of an accounting firm who reviews documents and exports batches across all of that firm's companies.
_Avoid_: Auditor, reviewer
