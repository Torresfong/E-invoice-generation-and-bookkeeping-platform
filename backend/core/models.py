from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.db.models.functions import Lower, Now
from django.utils.translation import gettext_lazy as _


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra):
        if not email:
            raise ValueError("An email address is required")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_superuser", True)
        return self.create_user(email, password, **extra)


class Firm(models.Model):
    """An accounting firm (D49, D67). A company may have no firm; each firm accountant belongs to exactly one.

    Created only by the platform operator. The web role can read its own firm and nothing else.
    """

    name = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(Lower("name"), name="firm_name_unique_ignoring_case")]

    def __str__(self):
        return self.name


class User(AbstractBaseUser, PermissionsMixin):
    """The sign-in identity: an email address and a password (D40 (5)).

    Which companies a user may act in, and as what, lives in `CompanyAccess`, not here.
    """

    email = models.EmailField(unique=True)
    is_active = models.BooleanField(default=True)
    # Marks a firm accountant for good, so one created before any company still gets access to later ones.
    # Set only by the operator command; the database default lets the SQL functions create users without it.
    is_accountant = models.BooleanField(default=False, db_default=False)
    # A firm administrator (D49) manages the firm's accountants and sees no client data. Held here, not on a
    # `CompanyAccess` row, and never together with `is_accountant`. Set only by the operator commands and the
    # firm administrator's own functions.
    is_firm_admin = models.BooleanField(default=False, db_default=False)
    # The firm of a firm accountant or administrator (D49); empty for clients. Set only by the operator commands.
    firm = models.ForeignKey(Firm, null=True, blank=True, on_delete=models.PROTECT, related_name="users")
    date_joined = models.DateTimeField(auto_now_add=True)

    objects = UserManager()
    USERNAME_FIELD = "email"

    class Meta:
        constraints = [
            models.UniqueConstraint(Lower("email"), name="user_email_unique_ignoring_case"),
            models.CheckConstraint(condition=~models.Q(is_accountant=True) | models.Q(firm__isnull=False), name="accountant_has_firm"),
            models.CheckConstraint(condition=~models.Q(is_firm_admin=True) | models.Q(firm__isnull=False), name="firm_admin_has_firm"),
            models.CheckConstraint(condition=~(models.Q(is_firm_admin=True) & models.Q(is_accountant=True)), name="not_accountant_and_firm_admin"),
        ]

    def __str__(self):
        return self.email


class Company(models.Model):
    """A tenant: one business whose data is isolated from every other company (D11)."""

    name = models.CharField(max_length=200)
    firm = models.ForeignKey(Firm, null=True, blank=True, on_delete=models.PROTECT, related_name="companies")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class CompanyAccess(models.Model):
    """Which users may act in which company, and in what role (D40).

    This table is what row-level security reads: a row of a tenant table is visible only when its
    company id is among the signed-in user's rows here. There is no bypass role.
    """

    OWNER = "owner"
    STAFF = "staff"
    ACCOUNTANT = "accountant"
    ROLES = [(OWNER, _("Client owner")), (STAFF, _("Client staff")), (ACCOUNTANT, _("Firm accountant"))]

    user = models.ForeignKey("core.User", on_delete=models.CASCADE, related_name="company_access")
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="access")
    role = models.CharField(max_length=20, choices=ROLES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "company"], name="one_access_row_per_user_company"),
            models.UniqueConstraint(
                fields=["company"], condition=models.Q(role="owner"), name="one_owner_per_company"
            ),
            models.CheckConstraint(condition=models.Q(role__in=["owner", "staff", "accountant"]), name="known_role"),
        ]


class TenantModel(models.Model):
    """Base for every business table. Pair it with `core.rls.enable_tenant_rls` in the migration."""

    company = models.ForeignKey(Company, on_delete=models.PROTECT, db_index=True)

    class Meta:
        abstract = True


class DocumentSequence(TenantModel):
    """The gapless number sequence of one company and document type (D18, D44, D59 (6)).

    `next_number` is the number the next approval takes. It moves forward by one, only through
    `core.numbering.allocate_number`, and the database refuses any other change once a number has been issued
    (migration 0034), so a number is never skipped, reused or taken back.
    """

    document_type = models.CharField(max_length=20)
    prefix = models.CharField(max_length=10, blank=True)
    start_number = models.BigIntegerField()
    next_number = models.BigIntegerField()
    # Zero-padded width of the digits, so an owner can continue "INV-00123"; 0 means no padding.
    padding = models.SmallIntegerField(default=0, db_default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["company", "document_type"], name="one_sequence_per_company_and_type"),
            models.CheckConstraint(condition=models.Q(padding__gte=0, padding__lte=10), name="sequence_padding_range"),
            models.CheckConstraint(condition=models.Q(start_number__gte=1), name="sequence_start_positive"),
            models.CheckConstraint(
                condition=models.Q(next_number__gte=models.F("start_number")), name="sequence_next_not_before_start"
            ),
        ]


class SupplierDetails(models.Model):
    """The supplier fields of a company's e-invoice setting, shared by the setting and each history row (D59 (2)).

    TIN, BRN and ROB identify the business to LHDN. They must never reach a log, an audit entry, a fixture or a
    `repr`, so `__str__` and `__repr__` show the company id only.
    """

    MANDATORY, VOLUNTARY, EXEMPT = "mandatory", "voluntary", "exempt"
    STATUS_CHOICES = [(MANDATORY, "Mandatory"), (VOLUNTARY, "Voluntary"), (EXEMPT, "Exempt")]

    einvoicing_enabled = models.BooleanField(default=False, db_default=False)
    # Empty until the exemption check of D63 has been done ("not yet checked").
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, null=True, blank=True)
    effective_from = models.DateField()
    tin = models.CharField(max_length=20, blank=True, default="")
    brn = models.CharField(max_length=20, blank=True, default="")
    rob = models.CharField(max_length=20, blank=True, default="")
    msic_code = models.CharField(max_length=5, blank=True, default="")
    address_line1 = models.CharField(max_length=150, blank=True, default="")
    address_line2 = models.CharField(max_length=150, blank=True, default="")
    city = models.CharField(max_length=50, blank=True, default="")
    postal_zone = models.CharField(max_length=5, blank=True, default="")
    state_code = models.CharField(max_length=2, blank=True, default="")
    country_code = models.CharField(max_length=3, blank=True, default="MYS")

    class Meta:
        abstract = True

    def __str__(self):
        return f"{type(self).__name__} (company {self.company_id})"

    __repr__ = __str__


class CompanyEInvoiceSetting(SupplierDetails, TenantModel):
    """The current e-invoice setting of a company: one row at most (D59 (2), D61 (3)).

    Only the company owner may write it (migration 0035 refuses anyone else), and every write goes through
    `core.einvoicing.update_setting`, which also keeps a `CompanyEInvoiceSettingChange`.
    """

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["company"], name="one_einvoice_setting_per_company")]


class CompanyEInvoiceSettingChange(SupplierDetails, TenantModel):
    """One change to a company's e-invoice setting, kept for good with its date and person (D59 (2), D63).

    Append-only: the database refuses to update or delete a row. `einvoicing_enabled(company, on_date)` reads the latest
    row whose effective date has arrived. `status` may be empty, so the first time a status is set shows in the history.
    """

    changed_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    changed_at = models.DateTimeField(db_default=Now())
    # Set when a mandatory company is switched off: who accepted the warning, when, and which wording they saw.
    acknowledged_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledgement_version = models.CharField(max_length=40, blank=True, default="")

    class Meta:
        indexes = [models.Index(fields=["company", "effective_from", "id"], name="einvoice_change_lookup")]


class Item(TenantModel):
    """A product or service a company sells, with the LHDN data every invoice line needs (D59 (9), (10)).

    The two LHDN codes and the default tax type are codes from LHDN's published lists (`core.lhdn_codes`). Only the owner
    changes the codes. An item is deactivated, never deleted, so it stays on every invoice it already appears on
    (migration 0036 refuses a delete and records each change in `ItemChange`).
    """

    name = models.CharField(max_length=200)
    classification_code = models.CharField(max_length=3)
    unit_code = models.CharField(max_length=10)
    default_tax_type = models.CharField(max_length=2)
    is_active = models.BooleanField(default=True, db_default=True)
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        indexes = [models.Index(fields=["company", "is_active", "name"], name="item_company_active_name")]
        ordering = ["name", "id"]

    def __str__(self):
        return self.name


class ItemChange(TenantModel):
    """One change to an item with its values before and after, written by a database trigger (ticket #121).

    Append-only. `before` is empty for the row that records the item's creation. `changed_by` is empty for an operator
    command, which runs with no signed-in user.
    """

    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="changes")
    changed_by = models.ForeignKey(User, null=True, on_delete=models.PROTECT, related_name="+")
    # A copy of the address, like `AuditEntry.actor_email`: the database lets a user read only their own user row, so a
    # colleague, or an accountant, could not otherwise see who made the change.
    changed_by_email = models.CharField(max_length=254, blank=True, db_default="")
    changed_at = models.DateTimeField(db_default=Now())
    before = models.JSONField(null=True)
    after = models.JSONField()

    class Meta:
        indexes = [models.Index(fields=["item", "id"], name="item_change_lookup")]
        ordering = ["id"]


class Job(models.Model):
    """A queued background job (D10, D36, ADR 0001).

    `company` is set when the job is queued, from the company the signed-in user is acting in, and is null
    only for system jobs that touch no tenant data. The worker takes the company context for a handler from
    this column and never from the payload. `payload` holds identifiers only and `error` holds only an
    exception class name, so the table never contains tenant content. The table is under row-level security
    and is not exposed in any screen or API.

    A worker claims a job by marking it running with a lease, runs the handler outside any row lock, and records
    the outcome only if it still holds the lease (ADR 0001, ticket #27; see core.jobs).
    """

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"

    company = models.ForeignKey(Company, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    name = models.CharField(max_length=100)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=10, default=PENDING, db_index=True)
    error = models.TextField(blank=True)
    # Counted when a worker claims the job, so a crashed attempt is counted without anyone having to record it.
    attempts = models.PositiveSmallIntegerField(default=0)
    # A pending job is not claimed before this time (null: now). Set by a retry's backoff.
    run_after = models.DateTimeField(null=True, blank=True)
    # A running job whose lease has expired belongs to a crashed or hung worker and is returned to pending.
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)


class LoginFailure(models.Model):
    """One failed sign-in attempt, for throttling. Only the hash of the email address is kept.

    The web role has no direct access to this table; it goes through the functions in migration 0007.
    """

    email_hash = models.CharField(max_length=64, db_index=True)
    at = models.DateTimeField(auto_now_add=True, db_index=True)
    # Set when a failed-sign-in audit entry has used this attempt as its proof: one attempt, at most one entry (D54).
    audited = models.BooleanField(default=False, db_default=False)


class AuthToken(models.Model):
    """A single-use, expiring link: a password reset, an invitation, a firm's sign-up link or a pending registration (D40 (5), D48, D49).

    Only the SHA-256 hash of the token is stored, so a database reader cannot use a link. The web role has
    no direct access to this table; it goes through the functions in migrations 0009 and 0010.
    """

    RESET = "reset"
    INVITE = "invite"
    SIGNUP = "signup"
    REGISTER = "register"

    PURPOSES = [
        (RESET, "Password reset"),
        (INVITE, "Invitation"),
        (SIGNUP, "Firm sign-up link"),
        (REGISTER, "Pending registration"),
    ]

    purpose = models.CharField(max_length=10, choices=PURPOSES)
    token_hash = models.CharField(max_length=64, unique=True)
    email = models.EmailField()
    company = models.ForeignKey(Company, null=True, blank=True, on_delete=models.CASCADE)
    role = models.CharField(max_length=20, blank=True)
    user = models.ForeignKey("core.User", null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    # A firm's sign-up link and the pending registration made through it carry the firm (D49 (4)); the pending
    # registration also holds the company name until the owner opens the emailed link.
    firm = models.ForeignKey(Firm, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    company_name = models.CharField(max_length=200, blank=True, db_default="")
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class AuditEntry(models.Model):
    """One line of the audit trail: who did what, to which record, when (D40 (6)).

    Append-only: the web role can insert and read but not change or delete, and a trigger refuses updates
    and deletes from anyone else. An update records the names of the fields that changed, never their
    values, so an entry cannot hold a credential, a token or a customer's details.
    """

    LOGIN = "login"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    VIEWED = "viewed"
    ACTIONS = [
        (LOGIN, _("Signed in")),
        (LOGIN_FAILED, _("Failed sign-in")),
        (LOGOUT, _("Signed out")),
        (CREATE, _("Created")),
        (UPDATE, _("Updated")),
        (DELETE, _("Deleted")),
        (VIEWED, _("Viewed")),
    ]

    company = models.ForeignKey(Company, null=True, on_delete=models.PROTECT, related_name="+")
    # A plain number and a copy of the address, not a foreign key: the entry must outlive any change to the account.
    actor_id = models.BigIntegerField(null=True, blank=True)
    actor_email = models.CharField(max_length=254, blank=True)
    action = models.CharField(max_length=20, choices=ACTIONS)
    target_type = models.CharField(max_length=100, blank=True)
    target_id = models.CharField(max_length=40, blank=True)
    changed_fields = models.JSONField(default=list)
    occurred_at = models.DateTimeField(db_default=Now())
    # Set once per request or job (D50 (6)); null only for entries written before 02k.
    correlation_id = models.UUIDField(
        null=True, blank=True, db_default=models.Func(function="audit_correlation_id", output_field=models.UUIDField())
    )

    class Meta:
        indexes = [models.Index(fields=["company", "-occurred_at"], name="audit_company_time")]
        ordering = ["-occurred_at", "-id"]


class FirmAuditEntry(models.Model):
    """One line of the firm audit trail: a firm-level event, kept apart from every company's trail (D50).

    Append-only and time-stamped by the database like `AuditEntry`, with the same field-names-only rule. The database
    lets a user read only their own firm's entries; limiting the page to firm administrators (D50 (2)) is the job of
    whatever screen shows them (#74). `actor_email` is "operator" for an operator command.
    """

    firm = models.ForeignKey(Firm, on_delete=models.PROTECT, related_name="+")
    actor_id = models.BigIntegerField(null=True, blank=True)
    actor_email = models.CharField(max_length=254, blank=True)
    action = models.CharField(max_length=20, choices=AuditEntry.ACTIONS)
    target_type = models.CharField(max_length=100, blank=True)
    target_id = models.CharField(max_length=40, blank=True)
    changed_fields = models.JSONField(default=list)
    occurred_at = models.DateTimeField(db_default=Now())
    # Set once per request or job (D50 (6)); the database supplies one when nothing has set it.
    correlation_id = models.UUIDField(db_default=models.Func(function="audit_correlation_id", output_field=models.UUIDField()))

    class Meta:
        indexes = [models.Index(fields=["firm", "-occurred_at"], name="firm_audit_firm_time")]
        ordering = ["-occurred_at", "-id"]
