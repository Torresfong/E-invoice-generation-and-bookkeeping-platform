"""Submitted invoices: one row per invoice the submission job has queued, recovered or had LHDN accept (#47).

Keyed by the invoice's id as a bare integer, never a Django `ForeignKey` into `core.models.Invoice`: `myinvois`
reaches the engine's tables only by id, so the two apps' migrations never need to order against each other
(D61 (4)). The row carries the company id under the same row-level security and audit pattern as every other
tenant table (docs/architecture/tenancy.md), so the submission job (running as the worker, in the job's own
company context) is the only writer and a cross-company lookup finds nothing.

The row serves two purposes with one write each:
- Before LHDN is called, it records the invoice number and the document's hash (D58 (5)): a retry that follows a
  lost response finds this row already there and looks the document up instead of submitting a second time.
- Once LHDN has answered, `uuid` and `recovered` hold the outcome. While `uuid` is blank the invoice is still
  "submitted (queued)" (D44); once it is set, LHDN has the document.
"""

from django.db import models
from django.db.models.functions import Now

from core.models import TenantModel


class SubmittedInvoice(TenantModel):
    invoice_id = models.BigIntegerField(unique=True)
    # Set before LHDN is ever called; a retry reads it back to tell whether an earlier attempt already ran.
    document_hash = models.CharField(max_length=64, blank=True, default="")
    # Blank while queued (an earlier attempt's answer never arrived); set once LHDN has a copy of the document.
    uuid = models.CharField(max_length=100, blank=True, default="")
    # True when the uuid was recovered by looking the document up, rather than returned directly by submit (D58 (5)).
    recovered = models.BooleanField(default=False, db_default=False)
    created_at = models.DateTimeField(db_default=Now())
