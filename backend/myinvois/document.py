"""Mapping an approved invoice to LHDN's UBL 2.1 JSON document (ticket #123, D14, D59, D60).

#123 maps ringgit invoices only (the brief's "common case"): per-line SST (#52) and foreign currency (#53) do not
exist yet, so every line's tax type is LHDN's "Not Applicable" code and its tax amount is zero; #125 adds real
per-line tax and foreign currency once #52 and #53 exist. The document currency is always MYR.

The mapper reads: the invoice and its locked buyer snapshot (`buyer_*`, frozen at approval by #46); the company's
e-invoice setting (`core.einvoicing.current_setting`); and each line's live item for its classification code and
unit code. #46's handoff note on this ticket flags that the item's codes are not frozen at approval and asks either
to freeze them on `InvoiceLine` or to record why reading them live is accepted. This ticket takes the second option:
the brief says a line "carries the classification code and unit code from its item" (present tense), only the owner
may change an item's codes (`change_item_codes`, audited in `ItemChange`), and freezing them is a larger, schema-
changing decision for the invoice engine that the brief does not ask for. A test below pins the current behaviour
(a code change after approval reaches the next mapping) so a future ticket that decides to freeze them changes an
assertion, not silently drifts.

Every supplier and buyer string (`_supplier_details`, `_buyer_details`) is stripped before it is checked or
written: PR #178's review of #172 found that a blank-looking (whitespace-only) optional value passes both Core's
own checks and `field_rules`, so the mapper must not forward one. An optional element whose stripped value is
empty is left off the document rather than written as "". A line's own strings (the locked `item_name`, and the
item's classification and unit codes) need no stripping here: `core.items.create_item`/`update_item` already
strip and save the item's name, and its codes are validated as an exact match against LHDN's code lists, so a
padded code is refused before it is ever stored.
"""

import json
from datetime import timezone as dt_timezone
from decimal import ROUND_HALF_UP, Decimal, localcontext
from uuid import uuid4

from core import einvoicing
from core.jobs import PermanentError
from core.models import Invoice

from .code_lists import msic_codes
from .field_rules import check_customer, check_einvoice_setting, check_line
from .submission import STALE_AFTER

# LHDN's Submit Documents page gives this as "300 KB maximum per e-Invoice", read here as the stricter decimal
# 300,000 bytes rather than the looser binary 300 * 1024 (LHDN's own wording wins where the two differ). D60, which
# governs this ticket's document rules, sets no size limit of its own; D66 (3) states the same 300 KB figure for a
# consolidated e-invoice. This checks only the unsigned document a job would pass to `submission.submit_invoice`:
# #43 adds a `UBLExtensions`/`Signature` block (the certificate among them), so the signed document needs the same
# check again, after signing, which this ticket does not build.
MAX_DOCUMENT_BYTES = 300_000

# D60 (1): "1.1" in production, "1.0" in the sandbox while unsigned. Not this function's choice (`map_invoice`'s
# caller picks one), but checked, since LHDN rejects any other value and a typo should not reach a built document.
DOCUMENT_VERSIONS = ("1.0", "1.1")

INVOICE_TYPE_CODE = "01"
NOT_APPLICABLE = "06"  # LHDN's tax-type code for "Not Applicable": the only one #123 ever emits (see module docstring).
TAX_SCHEME = {"_": "OTH", "schemeID": "UN/ECE 5153", "schemeAgencyID": "6"}

UBL_NAMESPACES = {
    "_D": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "_A": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "_B": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
}

CENTS = Decimal("0.01")

# `InvoiceLine.quantity` and `.unit_price` are each NUMERIC(18,4): up to 14 integer and 4 fractional digits
# (`core/models.py`). Their exact product can need up to 36 significant digits (14+14 integer, 4+4 fractional),
# more than Python's default 28-digit Decimal context. Multiplying in the ambient context would silently round
# the product under ROUND_HALF_EVEN before `.quantize(..., ROUND_HALF_UP)` ever saw it, for inputs well inside
# what `core.invoices._amount` already accepts, and `quantize` itself raises `InvalidOperation` once the exact
# product needs more digits than the context allows. 50 is comfortable headroom above the 36-digit maximum.
LINE_AMOUNT_PRECISION = 50


class InvalidDocument(PermanentError, ValueError):
    """The document cannot be built. The message names each field or rule that failed, never a value.

    A `core.jobs.PermanentError`: none of this function's own refusals (a bad field, an unsupported
    `document_version`, an oversized document) can fix itself on retry, so a job handler gets exactly one attempt
    (`core.jobs._after_failure`) instead of the three a plain, unclassified exception would get. These messages are
    operator- and log-facing only, in English regardless of the active language, unlike `field_rules`' own
    messages (joined into one of these below), which are translated. A caller that shows one to the owner (D60 (2)
    already does, for the same 72-hour rule, through `StaleIssueTime` below) must translate its own text instead of
    forwarding `str(exc)`.
    """


class StaleIssueTime(InvalidDocument):
    """The invoice's issue time is more than 72 hours old (D60 (2)); never raised for a future issue time.

    Handoff for #47: `submission.submit_invoice` already runs a recovery lookup for this exact rule (D58 (5), (6))
    before it gives up and tells the owner (`submission.StaleDocument`), because the document may already be at
    LHDN from an earlier attempt whose answer was lost. If a job calls `map_invoice` again after such a loss, more
    than 72 hours may have passed by then, and this subclass is raised before `submit_invoice` ever runs, so #47
    must either run that recovery lookup (or the same check `submit_invoice` does) before mapping, or catch this
    subclass specifically, not `InvalidDocument` generally, and take the same recovery path rather than treat it as
    an ordinary refusal.
    """


def map_invoice(invoice, *, document_version, now):
    """The minified UTF-8 JSON bytes of `invoice`'s LHDN document, or `InvalidDocument` naming what stops it.

    `invoice` must already be approved (D17, D19): a draft has no number and no locked buyer. It must be an
    `"invoice"` (credit, debit and refund notes are #124) and an e-invoice (`is_einvoice`): a normal invoice is
    never submitted to LHDN (D59 (3)), so this function refuses to map one rather than build a document nobody
    sends. `document_version` is "1.1" for production or "1.0" for the sandbox (D60 (1)); this function does not
    choose it. `now` is compared to the issue time (`invoice.approved_at`, D60 (2)): more than 72 hours old, or in
    the future, is refused.
    """
    if document_version not in DOCUMENT_VERSIONS:
        raise InvalidDocument("document_version")
    if invoice.status != Invoice.APPROVED:
        raise InvalidDocument("status: the invoice must be approved")
    if invoice.document_type != "invoice":
        raise InvalidDocument("document_type: only an invoice is mapped here; notes are #124")
    if not invoice.is_einvoice:
        raise InvalidDocument("is_einvoice: a normal invoice is never submitted to LHDN (D59 (3))")

    # `approved_at` is already UTC-aware (Django keeps every datetime in UTC internally with USE_TZ=True); converted
    # explicitly so the document's date and time are UTC (D60 (2)) regardless of `TIME_ZONE` (Asia/Kuala_Lumpur).
    issued_at = invoice.approved_at.astimezone(dt_timezone.utc)
    if issued_at > now:
        raise InvalidDocument("issue time is in the future")
    if now - issued_at > STALE_AFTER:
        raise StaleIssueTime("issue time is more than 72 hours old")

    setting = einvoicing.current_setting(invoice.company)
    if setting is None:
        raise InvalidDocument("e-invoice setting")
    supplier = _supplier_details(setting)
    buyer = _buyer_details(invoice)
    lines = list(invoice.lines.select_related("item"))
    if not lines:
        raise InvalidDocument("lines")

    # Every amount computed below (each line's subtotal, their sum, and the inclusive total built from that sum)
    # runs in one widened-precision context: `NUMERIC(18,4)` operands can need up to 36 significant digits (see
    # `LINE_AMOUNT_PRECISION`), more than Python's default 28-digit context, and that is true of the summation
    # and the final addition just as much as the per-line multiplication (Codex's second finding on PR #191: the
    # first fix only widened the multiplication, so a large sum or total still lost precision past that point).
    with localcontext() as ctx:
        ctx.prec = LINE_AMOUNT_PRECISION
        pairs = [(line, _line_details(line, invoice.number)) for line in lines]

        problems = list(check_einvoice_setting(supplier)) + list(check_customer(buyer))
        for _line, details in pairs:
            problems += list(check_line(details))
        if problems:
            # `check_einvoice_setting`, `check_customer` and `check_line` share field names (both parties have a
            # "city"), so the message, not the bare field key, says whose field it is (field_rules' labels already
            # distinguish "the company's city" from "the customer's city"); still never a value (field_rules' own
            # rule).
            raise InvalidDocument("; ".join(dict.fromkeys(str(problem.message) for problem in problems)))

        document = _build(invoice, supplier, buyer, pairs, issued_at, document_version)

    encoded = _encode(document)
    if len(encoded) > MAX_DOCUMENT_BYTES:
        raise InvalidDocument("document size")
    return encoded


def _strip(value):
    return (value or "").strip()


def _or_na(value):
    return _strip(value) or "NA"


def _supplier_details(setting):
    return {
        "name": _strip(setting.legal_name),
        "tin": _strip(setting.tin),
        "brn": _strip(setting.brn),
        "sst": _or_na(setting.sst_number),
        "ttx": _or_na(setting.tourism_tax_number),
        "msic_code": _strip(setting.msic_code),
        "phone": _strip(setting.phone),
        "address_line1": _strip(setting.address_line1),
        "address_line2": _strip(setting.address_line2),
        "city": _strip(setting.city),
        "postal_zone": _strip(setting.postal_zone),
        "state_code": _strip(setting.state_code),
        "country_code": _strip(setting.country_code),
    }


def _buyer_details(invoice):
    return {
        "legal_name": _strip(invoice.buyer_legal_name),
        "tin": _strip(invoice.buyer_tin),
        "id_type": _strip(invoice.buyer_id_type),
        "id_value": _strip(invoice.buyer_id_value),
        "sst": _or_na(invoice.buyer_sst_number),
        "phone": _strip(invoice.buyer_phone),
        "address_line1": _strip(invoice.buyer_address_line1),
        "address_line2": _strip(invoice.buyer_address_line2),
        "city": _strip(invoice.buyer_city),
        "postal_zone": _strip(invoice.buyer_postal_zone),
        "state_code": _strip(invoice.buyer_state_code),
        "country_code": _strip(invoice.buyer_country_code),
    }


def _line_details(line, invoice_number):
    # `InvoiceLine` stores no amount of its own (`core/models.py`), so this is the first place anything computes
    # one: quantity times unit price, rounded to the sen under ROUND_HALF_UP (1 x 0.005 rounds up to 0.01). No
    # D-number records that rounding rule, and the invoice engine's own PDF and export (#12, #60-#63) will need
    # the same total; raised for Torres on PR #191 rather than decided here, since moving the calculation into
    # `core` so every consumer shares one answer would touch the invoice engine, which this ticket does not.
    # Relies on its caller (`map_invoice`) to have already widened the Decimal context's precision: see the
    # comment there.
    subtotal = (line.quantity * line.unit_price).quantize(CENTS, rounding=ROUND_HALF_UP)
    return {
        "classification_code": line.item.classification_code,
        "description": line.item_name,
        "unit_code": line.item.unit_code,
        "unit_price": line.unit_price,
        # Fixed, not read from the item's own default_tax_type: #123 calculates no tax at all (see module docstring).
        "tax_type": NOT_APPLICABLE,
        "tax_amount": Decimal("0.00"),
        "subtotal": subtotal,
        "total_excluding_tax": subtotal,
        "currency_code": "MYR",
        "invoice_number": invoice_number,
    }


def _value(value, **attrs):
    return [{"_": value, **attrs}]


def _money(value):
    return _value(value, currencyID="MYR")


def _address(details):
    lines = [{"Line": _value(details["address_line1"])}]
    if details["address_line2"]:
        lines.append({"Line": _value(details["address_line2"])})
    address = {
        "AddressLine": lines,
        "CityName": _value(details["city"]),
        "CountrySubentityCode": _value(details["state_code"]),
        "Country": [{"IdentificationCode": _value(details["country_code"], listID="ISO3166-1", listAgencyID="6")}],
    }
    if details["postal_zone"]:
        address["PostalZone"] = _value(details["postal_zone"])
    return [address]


def _supplier_party(supplier):
    return [{
        "Party": [{
            "IndustryClassificationCode": _value(supplier["msic_code"], name=msic_codes()[supplier["msic_code"]]),
            "PartyIdentification": [
                {"ID": _value(supplier["tin"], schemeID="TIN")},
                {"ID": _value(supplier["brn"], schemeID="BRN")},
                {"ID": _value(supplier["sst"], schemeID="SST")},
                {"ID": _value(supplier["ttx"], schemeID="TTX")},
            ],
            "PostalAddress": _address(supplier),
            "PartyLegalEntity": [{"RegistrationName": _value(supplier["name"])}],
            "Contact": [{"Telephone": _value(supplier["phone"])}],
        }],
    }]


def _buyer_party(buyer):
    return [{
        "Party": [{
            "PartyLegalEntity": [{"RegistrationName": _value(buyer["legal_name"])}],
            "PartyIdentification": [
                {"ID": _value(buyer["tin"], schemeID="TIN")},
                {"ID": _value(buyer["id_value"], schemeID=buyer["id_type"])},
                {"ID": _value(buyer["sst"], schemeID="SST")},
            ],
            "PostalAddress": _address(buyer),
            "Contact": [{"Telephone": _value(buyer["phone"])}],
        }],
    }]


def _tax_category(tax_type):
    return [{"ID": _value(tax_type), "TaxScheme": [{"ID": [TAX_SCHEME]}]}]


def _invoice_line(number, line, details):
    return {
        "ID": _value(str(number)),
        "InvoicedQuantity": _value(line.quantity, unitCode=details["unit_code"]),
        "LineExtensionAmount": _money(details["total_excluding_tax"]),
        "TaxTotal": [{
            "TaxAmount": _money(details["tax_amount"]),
            "TaxSubtotal": [{
                "TaxableAmount": _money(details["total_excluding_tax"]),
                "TaxAmount": _money(details["tax_amount"]),
                "TaxCategory": _tax_category(details["tax_type"]),
            }],
        }],
        "Item": [{
            "CommodityClassification": [{"ItemClassificationCode": _value(details["classification_code"], listID="CLASS")}],
            "Description": _value(details["description"]),
        }],
        "Price": [{"PriceAmount": _money(line.unit_price)}],
        "ItemPriceExtension": [{"Amount": _money(details["subtotal"])}],
    }


def _totals(pairs):
    total_excluding_tax = sum((details["total_excluding_tax"] for _line, details in pairs), Decimal("0.00"))
    total_tax = sum((details["tax_amount"] for _line, details in pairs), Decimal("0.00"))
    return total_excluding_tax, total_tax


def _build(invoice, supplier, buyer, pairs, issued_at, document_version):
    total_excluding_tax, total_tax = _totals(pairs)
    total_including_tax = total_excluding_tax + total_tax
    doc_invoice = {
        "ID": _value(invoice.number),
        "IssueDate": _value(issued_at.strftime("%Y-%m-%d")),
        "IssueTime": _value(issued_at.strftime("%H:%M:%SZ")),
        "InvoiceTypeCode": _value(INVOICE_TYPE_CODE, listVersionID=document_version),
        "DocumentCurrencyCode": _value("MYR"),
        "AccountingSupplierParty": _supplier_party(supplier),
        "AccountingCustomerParty": _buyer_party(buyer),
        "TaxTotal": [{
            "TaxAmount": _money(total_tax),
            "TaxSubtotal": [{
                "TaxableAmount": _money(total_excluding_tax),
                "TaxAmount": _money(total_tax),
                "TaxCategory": _tax_category(NOT_APPLICABLE),
            }],
        }],
        "LegalMonetaryTotal": [{
            "LineExtensionAmount": _money(total_excluding_tax),
            "TaxExclusiveAmount": _money(total_excluding_tax),
            "TaxInclusiveAmount": _money(total_including_tax),
            "PayableAmount": _money(total_including_tax),
        }],
        "InvoiceLine": [_invoice_line(number, line, details) for number, (line, details) in enumerate(pairs, start=1)],
    }
    return {**UBL_NAMESPACES, "Invoice": [doc_invoice]}


def _encode(document):
    """`document` as minified UTF-8 JSON, each `Decimal` written as the exact number it holds, never a `float`.

    LHDN's sample files quote decimal amounts as bare JSON numbers, but `json` has no native way to write a
    `Decimal` as one without passing it through `float` first (risking rounding and scientific notation). This
    swaps each `Decimal` for a unique placeholder string, lets `json.dumps` quote it normally, then strips the
    quotes back off in the finished text, so the number that reaches LHDN is the exact text the `Decimal` holds.
    It trusts every `Decimal` it is given to be finite, not a `NaN` or an infinity, and never checks again: the
    amounts this module computes (`tax_amount`, `subtotal`, the totals) and the ones it only passes through
    (`unit_price`) have already passed `check_line`'s `_amount`, and `line.quantity` can never hold anything but a
    plain, bounded decimal, because `core.invoices._amount` refuses any text that is not one before it is stored.
    """
    sentinel = uuid4().hex
    tokens = {}

    def freeze(node):
        if isinstance(node, Decimal):
            key = f"{sentinel}{len(tokens)}"
            tokens[key] = format(node, "f")
            return key
        if isinstance(node, dict):
            return {name: freeze(value) for name, value in node.items()}
        if isinstance(node, list):
            return [freeze(value) for value in node]
        return node

    text = json.dumps(freeze(document), separators=(",", ":"), ensure_ascii=False)
    for key, raw in tokens.items():
        text = text.replace(f'"{key}"', raw)
    return text.encode("utf-8")
