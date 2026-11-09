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
Any invoice the platform generates for a company, including credit, debit and refund notes. It is an e-invoice or a normal invoice, depending on the company's e-invoice setting.
_Avoid_: Bill, document

**Item**:
A product or service a company sells, kept once with its LHDN classification code, unit code and default tax type, and chosen on invoice lines.
_Avoid_: Product, stock item, SKU

**E-invoice**:
An invoice that has been submitted to LHDN through MyInvois.
_Avoid_: Digital invoice

**Normal invoice**:
An invoice generated for a company with e-invoicing off. It is never submitted to LHDN and has no validation status or QR code.
_Avoid_: Paper invoice, plain invoice

**E-invoice setting**:
A company's own choice and details for LHDN e-invoicing: its status (mandatory, voluntary or exempt) and the date it takes effect, its TIN, BRN and ROB, and its supplier details.
_Avoid_: LHDN profile, e-invoice flag

**TIN**:
The tax identification number LHDN gives a taxpayer.
_Avoid_: Tax ID

**BRN**:
The business registration number of a company, as on its SSM registration.
_Avoid_: BIN, SSM number

**ROB**:
A registration number that LHDN, for some taxpayers, uses together with the TIN to identify them; we pass it as TIN:ROB when we act for such a taxpayer. The LHDN pages we read do not say what the abbreviation stands for.
_Avoid_: Branch number

**Intermediary**:
The company that submits e-invoices to LHDN on behalf of taxpayers. Here it is our platform company, not the accounting firm.
_Avoid_: Agent, provider

**Representation**:
A taxpayer's authority, granted in the official MyInvois portal with dates and permissions, for our platform company to act for it.
_Avoid_: Authorisation, consent

**UUID**:
The identifier LHDN gives a document it accepts.
_Avoid_: Document id

**Long ID**:
The temporary identifier LHDN gives a valid document, used with the UUID to build its validation link.
_Avoid_: Validation code

**Submission ID**:
The identifier LHDN gives a batch of documents sent in one submission.
_Avoid_: Batch id

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
An accounting firm. A company belongs to at most one firm and may have none; a firm's accountants reach only that firm's companies. Created only by the platform operator.
_Avoid_: Practice, agency, accounting company

**Firm accountant**:
A user of an accounting firm who reviews documents and exports batches across all of that firm's companies.
_Avoid_: Auditor, reviewer
