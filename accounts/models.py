from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import UserManager as _DjangoUserManager
from django.db import models
from django.utils import timezone
import secrets


class ActiveUserManager(_DjangoUserManager):
    """Default manager: automatically excludes soft-deleted users."""

    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class User(AbstractUser):
    ROLE_CHOICES = (
        ('admin', 'Admin'),
        ('supplier', 'Supplier'),
        ('stockist', 'Stockist'),
        # NOTE (Stage 1/2/6 rename): the 'distributor' role is the customer-facing
        # storefront (a shop) — shown to users as "Marketplace" (was "Enterprise"
        # until 2026-07-27). The 'wholesaler' role is the upstream wholesale
        # tier — shown to users as "Distributor".
        ('distributor', 'Marketplace'),
        ('wholesaler', 'Distributor'),
        ('customer', 'Customer'),
    )
    role = models.CharField(max_length=15, choices=ROLE_CHOICES, default='customer')
    roles = models.CharField(max_length=100, blank=True, default='',
                             help_text='All roles this user has, comma-separated')
    phone = models.CharField(max_length=15, blank=True)
    pincode = models.CharField(max_length=6, blank=True, help_text='Default delivery pincode')
    distributor = models.ForeignKey(
        'distributors.Distributor', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='customers'
    )
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.CharField(max_length=50, blank=True)
    # Hard delete = permanent archive (never a physical row delete, so order /
    # financial history stays intact). Set by the grace-period purge job or the
    # admin "Delete permanently" action. See kb/09-account-lifecycle.md.
    is_purged = models.BooleanField(default=False)
    purged_at = models.DateTimeField(null=True, blank=True)
    preferred_language = models.CharField(max_length=10, blank=True, default='')
    dark_mode = models.BooleanField(default=False)

    # objects = only active (non-deleted) users. Use User.all_objects for admin/recovery.
    objects = ActiveUserManager()
    all_objects = _DjangoUserManager()  # Includes soft-deleted users

    @classmethod
    def role_values(cls):
        return {value for value, _ in cls.ROLE_CHOICES}

    def _clean_roles_list(self, roles):
        """Normalize role list to known values, deduplicated, preserving order."""
        allowed = self.role_values()
        seen = set()
        cleaned = []
        for raw in roles:
            role = (raw or '').strip()
            if not role or role not in allowed or role in seen:
                continue
            seen.add(role)
            cleaned.append(role)
        return cleaned

    def get_roles_list(self):
        parsed = self._clean_roles_list((self.roles or '').split(','))
        if parsed:
            return parsed
        fallback = self._clean_roles_list([self.role])
        return fallback or ['customer']

    def primary_role(self):
        return self.get_roles_list()[0]

    def has_role(self, role):
        return role in self.get_roles_list()

    def has_multiple_roles(self):
        # Single-role policy: an account can never hold more than one role.
        return False

    def add_role(self, role):
        # Single-role policy: adding a role SETS it as the account's only role.
        self.set_roles([role])

    def set_roles(self, roles):
        # Single-role policy: an account holds exactly ONE role. Keep the first.
        cleaned = self._clean_roles_list(roles)
        if not cleaned:
            cleaned = ['customer']
        single = cleaned[0]
        self.roles = single
        self.role = single

    def clean(self):
        super().clean()
        from django.core.exceptions import ValidationError
        from accounts.utils import is_valid_username
        if self.username and not is_valid_username(self.username):
            raise ValidationError({'username': 'Username must be a valid mobile number or email ID.'})

    def save(self, *args, **kwargs):
        # Keep legacy role field and CSV roles in sync until role-table migration.
        self.set_roles(self.get_roles_list())
        super().save(*args, **kwargs)
        roles = self.get_roles_list()

        # If user is a distributor, always tag their customer account to their own profile
        if 'distributor' in roles:
            try:
                prof = self.distributor_profile
                if prof and self.distributor != prof:
                    self.distributor = prof
                    super().save(update_fields=['distributor'])
            except Exception:
                pass
        # If user has customer role and distributor role, ensure they're tagged to own profile
        elif 'customer' in roles and self.distributor:
            # Check if this customer's distributor user also has this user as their profile
            # (prevents circular issues)
            pass

    def is_admin(self):
        return self.has_role('admin') or self.is_superuser

    def is_supplier(self):
        return self.has_role('supplier')

    def is_distributor(self):
        return self.has_role('distributor')

    def is_wholesaler(self):
        # 'wholesaler' role == the wholesale "Distributor" tier (UI label).
        return self.has_role('wholesaler')

    def is_customer(self):
        return self.has_role('customer')

    def soft_delete(self, deleted_by=''):
        from django.utils import timezone
        self.is_deleted = True
        self.is_active = False
        self.deleted_at = timezone.now()
        self.deleted_by = deleted_by
        self.save()

    def restore(self):
        # A purged (hard-deleted) account can never be restored.
        if self.is_purged:
            return
        self.is_deleted = False
        self.is_active = True
        self.deleted_at = None
        self.deleted_by = ''
        self.save()

    def purge(self):
        """Hard delete = permanent archive. Keeps the row (history integrity),
        flags it purged so it can never be restored or shown again."""
        from django.utils import timezone
        self.is_deleted = True
        self.is_active = False
        if self.deleted_at is None:
            self.deleted_at = timezone.now()
        self.is_purged = True
        self.purged_at = timezone.now()
        self.save()


class PasswordResetRequest(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('resolved', 'Resolved'),
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reset_requests')
    token = models.CharField(max_length=64, unique=True, db_index=True, default=secrets.token_hex)
    requested_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    requested_ip = models.GenericIPAddressField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='resolved_resets'
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['-requested_at']

    def __str__(self):
        return f'Reset request for {self.user} — {self.status}'

    def is_expired(self):
        if not self.expires_at:
            return False
        return timezone.now() >= self.expires_at


class CustomerAddress(models.Model):
    LABEL_CHOICES = (
        ('Home', 'Home'),
        ('Work', 'Work'),
        ('Other', 'Other'),
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='delivery_addresses')
    label = models.CharField(max_length=20, choices=LABEL_CHOICES, default='Home')
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=15)
    door_number = models.CharField(max_length=50)
    street_details = models.CharField(max_length=200)
    locality = models.CharField(max_length=100)
    post_office = models.CharField(max_length=100, blank=True, help_text='Village / Post Office name')
    taluk = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=100)
    district = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    pincode = models.CharField(max_length=10)
    google_map_link = models.URLField(blank=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ['-is_default', '-pk']

    def __str__(self):
        return f"{self.label} - {self.name}"

    def get_full_address(self):
        parts = [self.door_number, self.street_details, self.locality, self.city, self.pincode]
        return ", ".join(p for p in parts if p)

    def save(self, *args, **kwargs):
        if self.is_default:
            CustomerAddress.objects.filter(user=self.user).update(is_default=False)
        super().save(*args, **kwargs)

