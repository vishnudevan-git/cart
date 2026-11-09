from django.db import models
from django.conf import settings
from products.models import Product, ProductVariant


class Stockist(models.Model):
    STATUS_CHOICES = (('active', 'Active'), ('suspended', 'Suspended'))
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='stockist_profile')
    company_name = models.CharField(max_length=200)
    area = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=15, blank=True)
    address = models.TextField(blank=True)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='active')
    created_at = models.DateTimeField(auto_now_add=True)
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

    def __str__(self):
        return self.company_name


class StockistStage(models.Model):
    """Custom charge stages defined by the stockist (create/edit/delete)."""
    DISPLAY_CHOICES = (
        ('product',  'Product-wise only'),
        ('supplier', 'Supplier-wise only'),
        ('both',     'Both (Product & Supplier)'),
    )
    stockist = models.ForeignKey(Stockist, on_delete=models.CASCADE, related_name='stages')
    name = models.CharField(max_length=100, help_text='e.g. Shipping, Packing, Cold Storage')
    display_in = models.CharField(max_length=10, choices=DISPLAY_CHOICES, default='both')
    is_active = models.BooleanField(default=True)
    order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order', 'name']
        unique_together = ('stockist', 'name')

    def __str__(self):
        return f'{self.stockist.company_name} — {self.name}'


class StockistInventory(models.Model):
    stockist = models.ForeignKey(Stockist, on_delete=models.CASCADE, related_name='inventory')
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name='stockist_inventory')
    quantity = models.PositiveIntegerField(default=0)
    # Per-item opt-out: this one product is never auto-restocked, whatever the
    # global settings say. High-value or slow-moving items get ordered by hand.
    auto_restock_exempt = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('stockist', 'variant')
        ordering = ['variant__product__name', 'variant__label']

    def __str__(self):
        return f'{self.stockist.company_name} | {self.variant} — {self.quantity} units'


class ProcessingCharge(models.Model):
    """
    Stockist adds named charges per stage.
    3 levels: global > supplier > product (product wins).
    Stage is a FK to StockistStage (custom, manageable by stockist).
    """
    CHARGE_TYPE_CHOICES = (
        ('fixed',   'Fixed ₹ per unit'),
        ('percent', '% of supplier price'),
        ('per_kg',  '₹ per kg (weight-based)'),
    )
    SCOPE_CHOICES = (
        ('global',   'Global (all suppliers & products)'),
        ('supplier', 'Supplier-wise'),
        ('product',  'Product-wise'),
    )
    stockist = models.ForeignKey(Stockist, on_delete=models.CASCADE, related_name='processing_charges')
    scope = models.CharField(max_length=10, choices=SCOPE_CHOICES, default='product')
    supplier = models.ForeignKey(
        'suppliers.Supplier', on_delete=models.CASCADE,
        null=True, blank=True, related_name='stockist_charges'
    )
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE,
        null=True, blank=True, related_name='processing_charges'
    )
    stage_obj = models.ForeignKey(
        StockistStage, on_delete=models.CASCADE,
        null=True, blank=True, related_name='charges'
    )
    charge_type = models.CharField(max_length=10, choices=CHARGE_TYPE_CHOICES, default='fixed')
    charge_value = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    description = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['stage_obj__order', 'stage_obj__name']

    def __str__(self):
        stage_label = self.stage_obj.name if self.stage_obj else '?'
        if self.scope == 'global':
            return f'{self.stockist.company_name} | GLOBAL | {stage_label}'
        if self.scope == 'supplier' and self.supplier:
            return f'{self.stockist.company_name} | {self.supplier.company_name} | {stage_label}'
        if self.scope == 'product' and self.product:
            return f'{self.stockist.company_name} | {self.product.name} | {stage_label}'
        return f'{self.stockist.company_name} | {stage_label}'

    def get_stage_display(self):
        return self.stage_obj.name if self.stage_obj else '?'

    def get_scope_display(self):
        return dict(self.SCOPE_CHOICES).get(self.scope, self.scope)

    def get_charge_type_display(self):
        return dict(self.CHARGE_TYPE_CHOICES).get(self.charge_type, self.charge_type)

    def calculate(self, supplier_price, unit_grams=0):
        from decimal import Decimal
        sp = Decimal(str(supplier_price))
        if self.charge_type == 'fixed':
            return self.charge_value
        elif self.charge_type == 'percent':
            return round(sp * self.charge_value / 100, 2)
        elif self.charge_type == 'per_kg' and unit_grams:
            kg = Decimal(str(unit_grams)) / 1000
            return round(self.charge_value * kg, 2)
        return Decimal('0')


def stockist_for(marketplace):
    """The Stockist that serves this Marketplace (shop).

    Stage 7 chain rule: a shop is supplied by its wholesale Distributor, and a
    Distributor buys from exactly ONE Stockist (``Wholesaler.stockist``). So the
    stockist that prices/serves a shop is the one assigned to its Distributor.

    Falls back to the first active Stockist when the chain isn't assigned yet —
    single-stockist installs keep working with zero configuration.
    """
    if marketplace is not None:
        wholesaler = getattr(marketplace, 'wholesaler', None)
        if wholesaler is not None and wholesaler.stockist_id:
            assigned = wholesaler.stockist
            if assigned and assigned.status == 'active':
                return assigned
    return Stockist.objects.filter(status='active').first()


def get_stockist_price(product, variant, stockist=None, enterprise=None):
    """
    Returns (cost_price, charges_breakdown) for a variant.

    cost_price = supplier price + stockist processing charges
                 (+ the wholesale Distributor's margin, if `enterprise` is given
                  and it is supplied by an active Wholesaler).

    `enterprise` is a distributors.Distributor (UI: "Marketplace"). When passed,
    its parent wholesale Distributor's margin is applied on top — this is the
    Marketplace's true cost, and the floor customers can never be charged below.
    Priority for charges: product > supplier > global (most specific per stage).
    """
    from decimal import Decimal
    supplier_price = variant.selling_price
    breakdown = []
    base = supplier_price

    # Stage 7: direct-supply products (e.g. eggs) go supplier → marketplace.
    # Neither the stockist nor the wholesale Distributor touches them, so
    # NEITHER earns a commission: the cost is the supplier price, full stop.
    if getattr(product, 'direct_supply', False):
        return base, breakdown

    if stockist:
        all_charges = ProcessingCharge.objects.filter(
            stockist=stockist, is_active=True
        ).select_related('supplier', 'product', 'stage_obj')

        stage_charges = {}
        for c in all_charges:
            if not c.stage_obj:
                continue
            key = c.stage_obj_id
            if c.scope == 'product' and c.product_id == product.pk:
                stage_charges[key] = c
            elif c.scope == 'supplier' and c.supplier_id == product.supplier_id:
                if key not in stage_charges or stage_charges[key].scope == 'global':
                    stage_charges[key] = c
            elif c.scope == 'global':
                if key not in stage_charges:
                    stage_charges[key] = c

        total_charge = Decimal('0')
        for key, c in stage_charges.items():
            amt = c.calculate(supplier_price, variant.unit_grams)
            total_charge += amt
            breakdown.append({
                'stage': c.get_stage_display(),
                'type': c.get_charge_type_display(),
                'value': c.charge_value,
                'amount': amt,
                'scope': c.get_scope_display(),
            })
        base = supplier_price + total_charge

    # Wholesale Distributor margin (new tier): charged BY WEIGHT or BY QUANTITY
    # (never a flat %). Applied on top of the stockist cost when this Marketplace
    # is supplied by an active Distributor (Wholesaler).
    if enterprise is not None:
        w = getattr(enterprise, 'wholesaler', None)
        if w and getattr(w, 'status', '') == 'active' and w.margin_value:
            value = Decimal(str(w.margin_value))
            if w.margin_mode == 'per_weight':
                kg = Decimal(str(variant.unit_grams or 0)) / Decimal('1000')
                add = (value * kg).quantize(Decimal('0.01'))
                mtype = '₹/kg'
            else:  # per_quantity
                add = value.quantize(Decimal('0.01'))
                mtype = '₹/unit'
            if add:
                breakdown.append({
                    'stage': 'Distributor margin',
                    'type': mtype,
                    'value': w.margin_value,
                    'amount': add,
                    'scope': 'wholesale',
                })
                base = base + add

    return base, breakdown


class StockistRestockRequest(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('fulfilled', 'Fulfilled'),
        ('rejected', 'Rejected'),
    )
    stockist = models.ForeignKey(Stockist, on_delete=models.CASCADE, related_name='restock_requests')
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name='stockist_restock_requests')
    quantity_requested = models.PositiveIntegerField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    note = models.CharField(max_length=200, blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    fulfilled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-requested_at']

    def __str__(self):
        return f'Restock: {self.variant} × {self.quantity_requested} — {self.status}'
