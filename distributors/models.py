from django.db import models
from django.conf import settings
from products.models import Product, ProductVariant


class Distributor(models.Model):
    """
    A distributor is a reseller who:
    - Gets products from admin at admin's price
    - Sets their own selling price to customers
    - Delivers to customers and sets delivery charges
    - Has their own pool of customers
    """
    STATUS_CHOICES = (
        ('active', 'Active'),
        ('suspended', 'Suspended'),
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='distributor_profile'
    )
    company_name = models.CharField(max_length=200)
    area = models.CharField(max_length=200, blank=True, help_text='Service area / locality')
    phone = models.CharField(max_length=15, blank=True)
    address = models.TextField(blank=True)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='active')
    # The wholesale Distributor (code: Wholesaler) that supplies this Marketplace.
    # One Wholesaler -> many Marketplaces. Nullable: existing/unassigned Marketplaces.
    wholesaler = models.ForeignKey(
        'distributors.Wholesaler', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='enterprises',
        help_text='The wholesale Distributor that supplies this Marketplace (shop)'
    )
    # Admin charges distributor this price (admin's margin is built in here)
    # Distributor adds their own margin on top when listing to customers
    default_margin_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
        help_text='Default % markup distributor adds on top of admin price'
    )
    delivery_charge = models.DecimalField(
        max_digits=8, decimal_places=2, default=0,
        help_text='Delivery charge distributor adds per order'
    )
    free_delivery_above = models.DecimalField(
        max_digits=8, decimal_places=2, default=0,
        help_text='Free delivery if order total exceeds this amount (0 = always charge)'
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
    invite_code = models.CharField(
        max_length=20, unique=True, blank=True,
        help_text='Customers use this code to register under this distributor'
    )
    logo = models.ImageField(upload_to='distributors/logos/', blank=True, null=True)
    tagline = models.CharField(max_length=200, blank=True, help_text='Short tagline shown to customers')
    theme_color = models.CharField(
        max_length=7, blank=True, default='#2e7d32',
        help_text='Primary theme color (hex). Shown to customers in navbar, buttons, etc.'
    )
    allow_partial_shipment = models.BooleanField(
        default=False,
        help_text='If enabled, available items are shipped immediately; unavailable items are back-ordered.'
    )
    hide_out_of_stock_products = models.BooleanField(
        default=True,
        help_text='If enabled, products with zero stock are hidden from customer view.'
    )
    # Auto-list new products when admin approves them
    auto_list_new_products = models.BooleanField(
        default=False,
        help_text='Automatically list newly approved products to customers using the default margin below.'
    )
    auto_list_margin_mode = models.CharField(
        max_length=10,
        choices=(('percent', '% of price'), ('per_unit', '₹ per unit weight')),
        default='percent',
    )
    auto_list_margin_value = models.DecimalField(
        max_digits=6, decimal_places=2, default=0,
        help_text='Margin value applied when auto-listing new products.'
    )
    auto_list_margin_cap_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=0, blank=True, null=True,
        help_text='Optional cap for auto-list margins (as % of base price).'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.company_name

    def save(self, *args, **kwargs):
        if not self.invite_code:
            import random, string
            self.invite_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        super().save(*args, **kwargs)
        
        # Single-role policy: an Marketplace (shop) is ONLY 'distributor' — it is
        # NOT auto-made a customer. (It sells; it doesn't shop its own store.)
        roles = self.user.get_roles_list()
        changed = False
        if roles != ['distributor']:
            self.user.set_roles(['distributor'])
            changed = True
        # Always tag the user's customer account to their own distributor profile
        if self.user.distributor != self:
            self.user.distributor = self
            changed = True
        if changed:
            self.user.save()


class DistributorProduct(models.Model):
    """
    A distributor selects which products to list and sets their own price.
    Admin price = what admin charges distributor (admin's cost + admin margin).
    Distributor price = what customer pays (admin price + distributor margin).
    """
    distributor = models.ForeignKey(Distributor, on_delete=models.CASCADE, related_name='dist_products')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='dist_products')
    is_listed = models.BooleanField(default=True, help_text='Show this product to customers')
    # Per-product margin override (if blank, uses distributor default_margin_percent)
    margin_percent = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text='Leave blank to use distributor default margin'
    )
    MARGIN_MODE_CHOICES = (
        ('percent', 'Percentage of admin price'),
        ('per_unit', '₹ per base unit (g/ml)'),
    )
    margin_mode = models.CharField(
        max_length=10, choices=MARGIN_MODE_CHOICES, default='percent',
        help_text='How margin is calculated: % of admin price, or fixed ₹ per gram/ml'
    )
    margin_is_manual = models.BooleanField(
        default=False,
        help_text='True when the distributor set this margin manually (so global/auto pricing will not override it).'
    )

    class Meta:
        unique_together = ('distributor', 'product')

    def __str__(self):
        return f'{self.distributor.company_name} — {self.product.name}'

    def effective_margin(self):
        if self.margin_percent is not None:
            return self.margin_percent
        return self.distributor.default_margin_percent


class DistributorVariantPrice(models.Model):
    """
    Distributor's final selling price for a specific variant.
    Base price = stockist price (supplier price + stockist processing charges).
    Distributor sets selling_price (what customer pays = base_price + distributor margin).
    """
    distributor = models.ForeignKey(Distributor, on_delete=models.CASCADE, related_name='variant_prices')
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name='dist_prices')
    base_price = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text='Stockist price (supplier price + processing charges). Auto-calculated.'
    )
    selling_price = models.DecimalField(max_digits=10, decimal_places=2)
    is_active = models.BooleanField(default=True)
    price_stale = models.BooleanField(
        default=False,
        help_text='True when supplier changed their price after this was last saved. Review needed.'
    )
    new_supplier_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text='Latest supplier price at time of price change notification'
    )
    stale_marked_at = models.DateTimeField(
        blank=True, null=True,
        help_text='Timestamp when price was marked stale. Used for reconciliation drift detection.'
    )

    class Meta:
        unique_together = ('distributor', 'variant')

    def __str__(self):
        return f'{self.distributor.company_name} | {self.variant} — ₹{self.selling_price}'

    def distributor_margin(self):
        """What distributor earns = selling_price - base_price."""
        return self.selling_price - self.base_price


class DeliverySlot(models.Model):
    DAY_CHOICES = (
        (0, 'Monday'), (1, 'Tuesday'), (2, 'Wednesday'),
        (3, 'Thursday'), (4, 'Friday'), (5, 'Saturday'), (6, 'Sunday'),
    )
    distributor = models.ForeignKey(Distributor, on_delete=models.CASCADE, related_name='delivery_slots')
    day_of_week = models.PositiveSmallIntegerField(choices=DAY_CHOICES)
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['day_of_week', 'start_time']
        unique_together = ('distributor', 'day_of_week', 'start_time')

    def __str__(self):
        return f'{self.get_day_of_week_display()} {self.start_time}–{self.end_time}'


class DistributorStock(models.Model):
    """Aggregate stock view — total quantity across all batches for quick lookup."""
    distributor = models.ForeignKey(Distributor, on_delete=models.CASCADE, related_name='stock_items')
    variant = models.ForeignKey(
        'products.ProductVariant', on_delete=models.CASCADE, related_name='distributor_stocks'
    )
    quantity = models.PositiveIntegerField(default=0)  # sum of all active batches
    # Per-item opt-out: this one product is never auto-restocked, whatever the
    # global settings say. High-value or slow-moving items get ordered by hand.
    auto_restock_exempt = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('distributor', 'variant')
        ordering = ['variant__product__name', 'variant__label']

    def __str__(self):
        return f'{self.distributor.company_name} | {self.variant} — {self.quantity} units'

    def refresh_quantity(self):
        """Recalculate total from batches."""
        from django.db.models import Sum
        total = self.batches.aggregate(t=Sum('quantity'))['t'] or 0
        self.quantity = total
        self.save(update_fields=['quantity'])


class DistributorStockBatch(models.Model):
    """
    One batch of stock received at a specific cost price.
    FIFO: oldest batch (lowest pk) consumed first.
    """
    stock = models.ForeignKey(DistributorStock, on_delete=models.CASCADE, related_name='batches')
    quantity = models.PositiveIntegerField(default=0)
    cost_price = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text='Admin/supplier price paid for this batch'
    )
    received_at = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['received_at']  # FIFO

    def __str__(self):
        return f'{self.stock.variant} batch @ ₹{self.cost_price} × {self.quantity}'


def consume_stock_fifo(distributor, variant, qty_needed):
    """
    Consume qty_needed units from distributor's batches FIFO.
    Returns list of (batch, qty_consumed, cost_price) tuples.
    Raises ValueError if insufficient stock.
    """
    try:
        stock = DistributorStock.objects.select_for_update().get(
            distributor=distributor, variant=variant
        )
    except DistributorStock.DoesNotExist:
        raise ValueError(f'No stock record for {variant}')

    if stock.quantity < qty_needed:
        raise ValueError(f'Insufficient stock: have {stock.quantity}, need {qty_needed}')

    consumed = []
    remaining = qty_needed
    for batch in stock.batches.select_for_update().filter(quantity__gt=0).order_by('received_at'):
        if remaining <= 0:
            break
        take = min(batch.quantity, remaining)
        batch.quantity -= take
        batch.save(update_fields=['quantity'])
        consumed.append((batch, take, batch.cost_price))
        remaining -= take

    stock.refresh_quantity()
    return consumed


class DistributorLooseStock(models.Model):
    """
    Distributor holds bulk/loose stock in base units (grams/ml).
    Any variant of the same product draws from this pool based on unit_grams.
    e.g. 5000g loose → can fulfill: 5×1kg, 10×500g, 20×250g, or any mix.
    """
    distributor = models.ForeignKey(Distributor, on_delete=models.CASCADE, related_name='loose_stocks')
    product = models.ForeignKey(
        'products.Product', on_delete=models.CASCADE, related_name='distributor_loose_stocks'
    )
    quantity_grams = models.PositiveIntegerField(
        default=0,
        help_text='Total loose stock in base units (grams/ml)'
    )
    cost_price_per_gram = models.DecimalField(
        max_digits=10, decimal_places=4, default=0,
        help_text='Cost per gram/ml paid by distributor'
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('distributor', 'product')

    def __str__(self):
        return f'{self.distributor.company_name} | {self.product.name} — {self.quantity_grams}g loose'

    def available_units(self, variant):
        """How many packs of this variant can be fulfilled from loose stock."""
        if not variant.unit_grams:
            return 0
        return self.quantity_grams // variant.unit_grams

    def deduct(self, variant, qty):
        """Deduct qty packs worth of grams. Returns grams deducted."""
        if not variant.unit_grams:
            return 0
        # Re-fetch with row lock to prevent concurrent deduction
        locked = DistributorLooseStock.objects.select_for_update().get(pk=self.pk)
        grams = variant.unit_grams * qty
        locked.quantity_grams = max(0, locked.quantity_grams - grams)
        locked.save(update_fields=['quantity_grams'])
        # Sync in-memory object
        self.quantity_grams = locked.quantity_grams
        return grams


class RestockRequest(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('approved', 'Approved by Stockist'),
        ('stockist_picked', 'Picked by Stockist'),
        ('in_transit', 'In Transit to Distributor'),
        ('delivered', 'Delivered to Distributor'),
        ('rejected', 'Rejected'),
    )
    FULFILLMENT_CHOICES = (
        ('stockist_pickup', 'Stockist will pick up'),
        ('supplier_transport', 'Supplier sends by transport'),
        ('supplier_courier', 'Supplier sends by courier'),
    )
    distributor = models.ForeignKey(Distributor, on_delete=models.CASCADE, related_name='restock_requests')
    variant = models.ForeignKey(
        'products.ProductVariant', on_delete=models.CASCADE, related_name='restock_requests'
    )
    quantity_requested = models.PositiveIntegerField()
    # Stage 7: >0 means this is a LOOSE request for that many grams — delivery
    # lands in DistributorLooseStock instead of packed batches. Loose stock is
    # never added directly any more: it must arrive through this request flow.
    loose_grams = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='pending')
    fulfillment_method = models.CharField(
        max_length=20, choices=FULFILLMENT_CHOICES, default='stockist_pickup', blank=True
    )
    tracking_info = models.CharField(
        max_length=200, blank=True,
        help_text='Courier tracking number or transport details'
    )
    note = models.CharField(max_length=200, blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-requested_at']

    def __str__(self):
        return f'{self.distributor.company_name} → {self.variant} × {self.quantity_requested}'


# Import reward models so they're part of this app
from distributors.reward_models import RewardConfig, RewardPoints, RewardTransaction
from distributors.wholesaler_models import Wholesaler

class DeliveryZone(models.Model):
    """
    Distributor defines delivery zones with different charges.
    Customers are assigned to a zone. If no zone assigned, distributor's default charge applies.
    """
    distributor = models.ForeignKey(Distributor, on_delete=models.CASCADE, related_name='delivery_zones')
    name = models.CharField(max_length=100, help_text='e.g. Zone A - Nearby, Zone B - City, Zone C - Outskirts')
    delivery_charge = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    free_delivery_above = models.DecimalField(
        max_digits=8, decimal_places=2, default=0,
        help_text='Free delivery if order exceeds this amount. 0 = always charge.'
    )
    pincodes = models.TextField(
        blank=True,
        help_text='Comma-separated pincodes covered by this zone (optional)'
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['delivery_charge']
        unique_together = ('distributor', 'name')

    def __str__(self):
        return f'{self.name} — ₹{self.delivery_charge}'

    def pincode_list(self):
        if self.pincodes:
            return [p.strip() for p in self.pincodes.split(',') if p.strip()]
        return []


class CustomerZone(models.Model):
    """Assigns a customer to a delivery zone under a distributor."""
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='delivery_zone_assignments'
    )
    distributor = models.ForeignKey(Distributor, on_delete=models.CASCADE, related_name='customer_zones')
    zone = models.ForeignKey(DeliveryZone, on_delete=models.CASCADE, related_name='customers')

    class Meta:
        unique_together = ('customer', 'distributor')

    def __str__(self):
        return f'{self.customer.username} → {self.zone.name}'


