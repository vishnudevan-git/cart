"""
Stage 8 — RENT: rentable equipment inside a marketplace, paid from a real-money
WALLET (loaded by paying the shop cash). The wallet is SEPARATE from reward
points (those stay in distributors/reward_models.py).

Whole feature is gated by one admin master switch: SiteSettings.rentals_enabled
(see rentals.services.rentals_enabled). Nothing here runs when it is off.
"""
from django.conf import settings
from django.db import models


# ── Money: the wallet (cash-loaded, separate from reward points) ─────────────
class Wallet(models.Model):
    """A customer's real-money balance at ONE shop.

    Loaded by paying the marketplace (cash for now) and spent on rent. Mirrors
    the reward-points shape — per (customer, distributor) — but is money, not
    points, and the two never mix.
    """
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='wallets'
    )
    distributor = models.ForeignKey(
        'distributors.Distributor', on_delete=models.CASCADE, related_name='wallets'
    )
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('customer', 'distributor')

    def __str__(self):
        return f'{self.customer.username} @ {self.distributor.company_name} — ₹{self.balance}'


class WalletTransaction(models.Model):
    """Every credit/debit on a wallet — an auditable money ledger."""
    KIND_CHOICES = (
        ('load',      'Loaded (cash paid to shop)'),
        ('rent_pay',  'Rent paid'),
        ('shop_pay',  'Shop order paid'),
        ('rent_earn', 'Rent earned'),
        ('refund',    'Refund'),
        ('damage',    'Damage / loss charge'),
        ('adjust',    'Manual adjustment'),
    )
    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name='transactions')
    kind = models.CharField(max_length=12, choices=KIND_CHOICES)
    amount = models.DecimalField(max_digits=10, decimal_places=2,
                                 help_text='Positive = credit, negative = debit')
    note = models.CharField(max_length=200, blank=True)
    booking = models.ForeignKey(
        'rentals.RentalBooking', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='wallet_entries'
    )
    created_by = models.CharField(max_length=60, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


# ── The rentable item ────────────────────────────────────────────────────────
class RentalItem(models.Model):
    """A machine/tool listed for rent inside ONE shop.

    Published by the shop (live at once), a customer, or the shop's Distributor
    (both need the shop's approval). Private to that one shop's rentals.
    """
    STATUS_CHOICES = (
        ('pending',  'Pending approval'),
        ('approved', 'Approved'),
        ('hidden',   'Hidden'),
    )
    OWNER_KIND_CHOICES = (
        ('shop',        'Marketplace'),
        ('customer',    'Customer'),
        ('distributor', 'Distributor'),
    )
    CATEGORY_CHOICES = (
        ('electrical', 'Electrical hardware'),
        ('cleaning',   'Cleaning machine'),
        ('machine',    'Machine'),
        ('tool',       'Tool'),
        ('other',      'Other'),
    )
    # The shop this item lives in — its rentals are private to this shop.
    distributor = models.ForeignKey(
        'distributors.Distributor', on_delete=models.CASCADE, related_name='rental_items'
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='rental_items'
    )
    owner_kind = models.CharField(max_length=12, choices=OWNER_KIND_CHOICES)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=12, choices=CATEGORY_CHOICES, default='other')
    image = models.ImageField(upload_to='rentals/', blank=True)
    rent_per_day = models.DecimalField(max_digits=10, decimal_places=2,
                                       help_text='Wallet ₹ charged per day')
    item_cost = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text='Replacement cost the RENTER pays if it is damaged or lost'
    )
    units = models.PositiveIntegerField(default=1, help_text='How many can be out at once')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['distributor', 'status', 'is_deleted'])]

    def __str__(self):
        return f'{self.name} @ {self.distributor.company_name} ({self.get_status_display()})'

    @property
    def is_live(self):
        return self.status == 'approved' and not self.is_deleted


class RentalBlock(models.Model):
    """A single day the owner marked unavailable (they are away)."""
    item = models.ForeignKey(RentalItem, on_delete=models.CASCADE, related_name='blocks')
    day = models.DateField()

    class Meta:
        unique_together = ('item', 'day')

    def __str__(self):
        return f'{self.item.name} blocked {self.day}'


class RentalBooking(models.Model):
    """One booking of an item for a run of days.

    A customer pays from their wallet at booking time; a marketplace or
    Distributor renter settles offline (the amount is recorded). The renter —
    never the shop — is liable for the item's cost on damage or loss.
    """
    STATUS_CHOICES = (
        ('requested', 'Requested'),
        ('confirmed', 'Confirmed — taken'),
        ('completed', 'Completed'),
        ('rejected',  'Rejected'),
        ('cancelled', 'Cancelled'),
    )
    RENTER_KIND_CHOICES = RentalItem.OWNER_KIND_CHOICES
    # Statuses that still hold the calendar slot (a unit is committed).
    ACTIVE_STATUSES = ('requested', 'confirmed')

    item = models.ForeignKey(RentalItem, on_delete=models.CASCADE, related_name='bookings')
    renter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='rental_bookings'
    )
    renter_kind = models.CharField(max_length=12, choices=RENTER_KIND_CHOICES)
    start_day = models.DateField()
    days = models.PositiveIntegerField(default=1)
    rate_per_day = models.DecimalField(max_digits=10, decimal_places=2)   # snapshot
    amount = models.DecimalField(max_digits=10, decimal_places=2)          # rate × days
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='requested')

    # Contact shared with the owner so they can call the renter.
    renter_phone = models.CharField(max_length=20, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['item', 'status'])]

    def __str__(self):
        return f'{self.item.name} · {self.renter.username} · {self.start_day} ×{self.days} [{self.status}]'

    @property
    def end_day(self):
        from datetime import timedelta
        return self.start_day + timedelta(days=self.days - 1)

    def covers_days(self):
        from datetime import timedelta
        return [self.start_day + timedelta(days=i) for i in range(self.days)]

    @property
    def paid_from_wallet(self):
        """Customers pay from the wallet; businesses settle offline. Derived
        from renter_kind (set once at booking, never changes)."""
        return self.renter_kind == 'customer'

    @property
    def owner_gets_wallet_credit(self):
        """Rent lands in the owner's wallet only when BOTH:
          * the renter actually paid into the wallet (a customer), and
          * the owner is another customer (a wallet holder).
        A business renter pays offline (no in-system money to move); a
        shop-owner keeps its own revenue; a Distributor-owner settles offline.
        Zero commission in every case."""
        return self.paid_from_wallet and self.item.owner_kind == 'customer'
