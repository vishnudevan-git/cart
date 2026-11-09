"""
Wholesaler = the new upstream WHOLESALE tier (shown to users as "Distributor").

NAMING MAP (internal code vs. what users see):
  - code `Distributor`  -> UI label "Marketplace" (the storefront with customers)
  - code `Wholesaler`   -> UI label "Distributor" (this new wholesale tier)

A Wholesaler sits between the Stockist and the Marketplaces. One Wholesaler
supplies MANY Marketplaces (via Distributor.wholesaler FK). Each Marketplace stays
fully independent (its own customers/stock/prices) — no interconnection between
Marketplaces under the same Wholesaler.
"""
from django.conf import settings
from django.db import models


class Wholesaler(models.Model):
    STATUS_CHOICES = (
        ('active', 'Active'),
        ('suspended', 'Suspended'),
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='wholesaler_profile'
    )
    company_name = models.CharField(max_length=200)
    area = models.CharField(max_length=200, blank=True, help_text='Region / territory served')
    phone = models.CharField(max_length=15, blank=True)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='active')

    # Wholesale margin — the Distributor charges by WEIGHT or by QUANTITY (never
    # a flat %). It does no processing; this margin is its earning for supplying
    # its shops. Added on top of the stockist cost (Stage 3 two-margin pricing).
    MARGIN_MODE_CHOICES = (
        ('per_quantity', 'Per quantity (₹ per unit/pack)'),
        ('per_weight', 'Per weight (₹ per kg)'),
    )
    margin_mode = models.CharField(
        max_length=15, choices=MARGIN_MODE_CHOICES, default='per_quantity',
        help_text='How this Distributor charges its margin'
    )
    margin_value = models.DecimalField(
        max_digits=8, decimal_places=2, default=0,
        help_text='₹ per unit (quantity mode) or ₹ per kg (weight mode)'
    )
    # Stage 7: one Stockist serves MANY Distributors, but a Distributor buys
    # from exactly ONE Stockist. Assigned by admin. Null = not assigned yet
    # (falls back to the first active Stockist so old installs keep working).
    stockist = models.ForeignKey(
        'stockist.Stockist', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='wholesalers',
        help_text='The one Stockist this Distributor buys from'
    )
    # Stage 7g: auto-restock — when stock dips to the threshold, the system
    # files the restock REQUEST automatically. It never adds stock directly:
    # the increase still arrives only when the request is delivered.
    auto_restock_enabled = models.BooleanField(default=False)
    auto_restock_threshold = models.PositiveIntegerField(
        default=5, help_text='Trigger when stock falls to this many units')
    auto_restock_quantity = models.PositiveIntegerField(
        default=10, help_text='How many units each auto request orders')
    auto_restock_max_price = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text='Skip items whose supplier price is above this (0 = no limit). '
                  'Stops high-value products from being auto-ordered.')
    # Legacy flat-% field kept for data history; no longer used in pricing.
    default_margin_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def margin_display(self):
        if self.margin_mode == 'per_weight':
            return f'₹{self.margin_value}/kg'
        return f'₹{self.margin_value}/unit'

    class Meta:
        verbose_name = 'Distributor (wholesale)'
        verbose_name_plural = 'Distributors (wholesale)'
        ordering = ['company_name']

    def enterprise_count(self):
        return self.enterprises.count()

    def __str__(self):
        return f'{self.company_name} (Distributor)'


class WholesalerStock(models.Model):
    """Stage 7: the wholesale Distributor's OWN stock ledger (optional to use).

    Stock rules — same as every non-source tier:
      * INCREASES only when a restock order from its stockist is delivered
        (WholesalerRestockRequest below). Never manually.
      * DECREASES may be manual (offline sales, own use, wastage) — the owner
        can always reduce, never inflate.
    """
    wholesaler = models.ForeignKey(Wholesaler, on_delete=models.CASCADE, related_name='stock_items')
    variant = models.ForeignKey(
        'products.ProductVariant', on_delete=models.CASCADE, related_name='wholesaler_stocks'
    )
    quantity = models.PositiveIntegerField(default=0)
    # Per-item opt-out: this one product is never auto-restocked, whatever the
    # global settings say. High-value or slow-moving items get ordered by hand.
    auto_restock_exempt = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('wholesaler', 'variant')
        ordering = ['variant__product__name', 'variant__label']

    def __str__(self):
        return f'{self.wholesaler.company_name} | {self.variant} — {self.quantity} units'


class WholesalerRestockRequest(models.Model):
    """A wholesale Distributor asks ITS one stockist for stock (Stage 7).

    The stockist that sees and fulfils this request is fixed at creation time
    (``stockist``) — a Distributor can never buy from a second stockist.
    Delivery is the ONLY way WholesalerStock increases.
    """
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('delivered', 'Delivered'),
        ('rejected', 'Rejected'),
    )
    wholesaler = models.ForeignKey(Wholesaler, on_delete=models.CASCADE, related_name='restock_requests')
    stockist = models.ForeignKey(
        'stockist.Stockist', on_delete=models.CASCADE, related_name='wholesaler_restock_requests'
    )
    variant = models.ForeignKey(
        'products.ProductVariant', on_delete=models.CASCADE, related_name='wholesaler_restock_requests'
    )
    quantity_requested = models.PositiveIntegerField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    note = models.CharField(max_length=200, blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-requested_at']

    def __str__(self):
        return f'{self.wholesaler.company_name}: {self.variant} × {self.quantity_requested} — {self.status}'
