"""The item master: the products and services a company sells (D59 (9), (10); ticket #121).

Each item carries the LHDN classification code, unit code and default tax type that every invoice line needs, so
the invoice screen (#44) and the LHDN mapping (#120) read them from the item and nobody types them per invoice.

Staff and the owner create items and edit the other fields; only the owner changes the two LHDN codes. Codes are
checked against LHDN's published lists (`core.lhdn_codes`). An item is deactivated, never deleted, and every change
is kept with its before and after values in `ItemChange`. The database holds the same rules (migration 0036), so a
writer that skips this module still cannot let staff change a code, delete an item or leave a change unrecorded.
"""

from django.db import transaction
from django.utils.translation import gettext as _

from . import permissions
from .lhdn_codes import classification_codes, tax_types, unit_codes
from .models import Item, ItemChange

MAX_NAME_LENGTH = 200
LHDN_CODE_FIELDS = ("classification_code", "unit_code")

__all__ = [
    "InvalidItem", "ItemNotAvailable", "classification_codes", "create_item", "deactivate_item", "history",
    "item_for_new_draft", "list_items", "reactivate_item", "tax_types", "unit_codes", "update_item",
]


class InvalidItem(ValueError):
    """A field is missing or not on LHDN's list. The message names the field and never its value."""


class ItemNotAvailable(LookupError):
    """The item does not exist for this company, or it has been deactivated and cannot go on a new draft."""


def _labels():
    return {
        "name": _("name"),
        "classification_code": _("classification code"),
        "unit_code": _("unit code"),
        "default_tax_type": _("default tax type"),
    }


def _validate(*, name, classification_code, unit_code, default_tax_type):
    labels = _labels()
    if not name.strip() or len(name) > MAX_NAME_LENGTH:
        raise InvalidItem(_("Check the %(field)s.") % {"field": labels["name"]})
    for field, value, known in (
        ("classification_code", classification_code, classification_codes()),
        ("unit_code", unit_code, unit_codes()),
        ("default_tax_type", default_tax_type, tax_types()),
    ):
        if value not in known:
            raise InvalidItem(_("Check the %(field)s.") % {"field": labels[field]})


def list_items(company, *, active_only=False):
    """The company's items by name. Row-level security already hides every other company's."""
    items = Item.objects.filter(company=company)
    return items.filter(is_active=True) if active_only else items


def create_item(user, company, *, name, classification_code, unit_code, default_tax_type):
    """Add an item as `user`, who must be the owner or staff of `company`.

    Raises `PermissionDenied` for anyone else and `InvalidItem` for a blank name or a code that is not on LHDN's list.
    """
    permissions.require(user, "manage_items", company)
    name = name.strip()
    _validate(
        name=name, classification_code=classification_code, unit_code=unit_code, default_tax_type=default_tax_type
    )
    return Item.objects.create(
        company=company,
        name=name,
        classification_code=classification_code,
        unit_code=unit_code,
        default_tax_type=default_tax_type,
    )


def update_item(user, item, *, name=None, classification_code=None, unit_code=None, default_tax_type=None):
    """Change the given fields of `item` as `user`; a field left as None stays as it is.

    The owner and staff may change the name and the default tax type; only the owner may change a classification or
    unit code that differs from the saved one. Raises `PermissionDenied` otherwise and `InvalidItem` for a blank name or
    a code that is not on LHDN's list. Saving values that equal the saved ones changes nothing and adds no history.
    """
    permissions.require(user, "manage_items", item.company)
    with transaction.atomic():
        current = Item.objects.select_for_update().get(pk=item.pk)
        wanted = {
            "name": current.name if name is None else name.strip(),
            "classification_code": current.classification_code if classification_code is None else classification_code,
            "unit_code": current.unit_code if unit_code is None else unit_code,
            "default_tax_type": current.default_tax_type if default_tax_type is None else default_tax_type,
        }
        _validate(**wanted)
        if any(wanted[field] != getattr(current, field) for field in LHDN_CODE_FIELDS):
            permissions.require(user, "change_item_codes", current.company)
        for field, value in wanted.items():
            setattr(current, field, value)
        current.save(update_fields=list(wanted))
    item.refresh_from_db()
    return item


def _set_active(user, item, active):
    permissions.require(user, "manage_items", item.company)
    Item.objects.filter(pk=item.pk).update(is_active=active)
    item.refresh_from_db()
    return item


def deactivate_item(user, item):
    """Stop `item` being chosen on new drafts. It stays on every invoice it already appears on (D59 (10))."""
    return _set_active(user, item, False)


def reactivate_item(user, item):
    return _set_active(user, item, True)


def item_for_new_draft(company, item_id):
    """The item a new draft line may use: it belongs to `company` and is active; otherwise `ItemNotAvailable`."""
    try:
        return Item.objects.get(company=company, pk=item_id, is_active=True)
    except Item.DoesNotExist:
        raise ItemNotAvailable from None


def history(item):
    """Every change to `item`, oldest first, each with its values before and after and who made it."""
    return list(ItemChange.objects.filter(item=item).select_related("changed_by"))
