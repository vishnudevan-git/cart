from decimal import Decimal

from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from orders.models import Order, OrderItem
from suppliers.models import Supplier


class CommissionRule(models.Model):
    """
    Flexible commission system. Rules are evaluated in priority order:
      1. product_variant  (most specific)
      2. product
      3. supplier
      4. overall (global fallback)
    """
    TYPE_CHOICES = (
        ('overall', 'Overall (Global Default)'),
        ('supplier', 'Supplier-wise'),
        ('product', 'Product-wise (flat %)'),
        ('product_price', 'Product-wise by Price (₹ amount)'),
        ('product_weight', 'Product-wise by Weight/Volume (₹ per unit)'),
    )
    rule_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, null=True, blank=True, related_name='commission_rules'
    )
    product = models.ForeignKey(
        'products.Product', on_delete=models.CASCADE, null=True, blank=True, related_name='commission_rules'
    )
    # For percentage-based rules
    rate = models.DecimalField(max_digits=5, decimal_places=2, default=0,
                               validators=[MinValueValidator(0), MaxValueValidator(100)],
                               help_text='Commission % (used for overall, supplier, product types)')
    # For fixed-amount rules
    fixed_amount = models.DecimalField(max_digits=8, decimal_places=2, default=0,
                                       validators=[MinValueValidator(0)],
                                       help_text='Fixed ₹ amount (used for product_price / product_weight types)')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['rule_type']

    def __str__(self):
        if self.rule_type == 'overall':
            return f'Overall — {self.rate}%'
        if self.rule_type == 'supplier' and self.supplier:
            return f'Supplier: {self.supplier.company_name} — {self.rate}%'
        if self.rule_type == 'product' and self.product:
            return f'Product: {self.product.name} — {self.rate}%'
        if self.rule_type == 'product_price' and self.product:
            return f'Product Price: {self.product.name} — ₹{self.fixed_amount}'
        if self.rule_type == 'product_weight' and self.product:
            return f'Product Weight: {self.product.name} — ₹{self.fixed_amount}/unit'
        return f'{self.get_rule_type_display()}'


def resolve_commission(product, supplier, selling_price):
    """
    Returns (rate_percent, fixed_amount, rule_type_used).
    Priority: product > supplier > overall
    """
    # 1. Product-specific rule
    rule = CommissionRule.objects.filter(
        is_active=True, product=product,
        rule_type__in=['product', 'product_price', 'product_weight']
    ).first()
    if rule:
        if rule.rule_type == 'product':
            return rule.rate, None, rule.rule_type
        return None, rule.fixed_amount, rule.rule_type

    # 2. Supplier-specific rule
    rule = CommissionRule.objects.filter(
        is_active=True, supplier=supplier, rule_type='supplier'
    ).first()
    if rule:
        return rule.rate, None, 'supplier'

    # 3. Overall global rule
    rule = CommissionRule.objects.filter(is_active=True, rule_type='overall').first()
    if rule:
        return rule.rate, None, 'overall'

    # 4. Fallback: no CommissionRule configured — return 0 (no platform commission).
    return 0, None, 'no_rule'


class Payment(models.Model):
    # COD is the only supported method for customer orders. Legacy 'upi'/'card'
    # labels are kept so any historical rows still render, but new orders are
    # always COD (enforced in orders.views.checkout).
    METHOD_CHOICES = (
        ('cod', 'Cash on Delivery'),
        ('upi', 'UPI (legacy)'),
        ('card', 'Card (legacy)'),
    )
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('paid', 'Paid'),
        ('failed', 'Failed'),
        ('refunded', 'Refunded'),
    )
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name='payment')
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    payment_method = models.CharField(max_length=10, choices=METHOD_CHOICES, default='cod')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    transaction_reference = models.CharField(max_length=100, blank=True)
    received_note = models.CharField(max_length=200, blank=True)
    # amount_collected: cumulative cash collected so far (via one or more partial deliveries).
    # Starts at 0 and accumulates as batches of items are delivered COD.
    # When amount_collected >= amount the status is flipped to 'paid'.
    amount_collected = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text='Running total actually collected (COD partial deliveries).')
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def amount_outstanding(self):
        from decimal import Decimal
        return max(Decimal('0'), self.amount - self.amount_collected)

    @property
    def is_fully_collected(self):
        return self.amount_collected >= self.amount

    def __str__(self):
        return f'Payment for Order #{self.order.id}'


class PaymentInstalment(models.Model):
    """One cash-collection event during a partial (or full) COD delivery.

    When a shop delivers a batch of items from an order and collects cash,
    a PaymentInstalment is created recording exactly which items were delivered
    and how much was collected. Multiple instalments can exist per order
    (one per delivery batch). When the cumulative amount_collected on Payment
    reaches the order total, the payment status flips to 'paid'.
    """
    payment = models.ForeignKey(
        Payment, on_delete=models.CASCADE, related_name='instalments')
    amount = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(0)],
        help_text='Cash collected in this delivery batch.')
    collected_at = models.DateTimeField(auto_now_add=True)
    collected_by = models.CharField(max_length=100, blank=True,
                                    help_text='Username of the shop user who collected.')
    note = models.CharField(max_length=200, blank=True)
    # Which order items were delivered in this batch (many-to-many read via FK
    # on OrderItem.delivery_instalment; set at delivery time).

    class Meta:
        ordering = ['collected_at']

    def __str__(self):
        return (f'Instalment ₹{self.amount} for Order #{self.payment.order_id}'
                f' at {self.collected_at:%d %b %Y %H:%M}')


class PaymentLedgerEntry(models.Model):
    PARTY_CHOICES = (
        ('customer', 'Customer'),
        ('distributor', 'Distributor'),
        ('stockist', 'Stockist'),
    )
    ENTRY_TYPE_CHOICES = (
        ('customer_payment', 'Customer Payment'),
        ('distributor_margin', 'Distributor Product Margin'),
        ('distributor_delivery_income', 'Distributor Delivery Income'),
        ('stockist_processing_charge', 'Stockist Processing Charge'),
    )
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('accrued', 'Accrued'),
        ('settled', 'Settled'),
        ('reversed', 'Reversed'),
    )

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='ledger_entries')
    payment = models.ForeignKey(
        Payment, on_delete=models.SET_NULL, null=True, blank=True, related_name='ledger_entries'
    )
    party_role = models.CharField(max_length=20, choices=PARTY_CHOICES)
    entry_type = models.CharField(max_length=40, choices=ENTRY_TYPE_CHOICES)
    distributor = models.ForeignKey(
        'distributors.Distributor', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='ledger_entries'
    )
    stockist = models.ForeignKey(
        'stockist.Stockist', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='ledger_entries'
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('order', 'party_role', 'entry_type')
        ordering = ['order_id', 'party_role', 'entry_type']

    def __str__(self):
        return f'Order #{self.order_id} {self.party_role} {self.entry_type} - Rs.{self.amount}'


class RoleSettlement(models.Model):
    STAGE_CHOICES = (
        ('distributor_to_stockist', 'Distributor to Stockist'),
        ('stockist_to_supplier', 'Stockist to Supplier'),
    )
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('submitted', 'Submitted for Confirmation'),
        ('confirmed', 'Confirmed'),
        ('disputed', 'Query Raised'),
        ('cancelled', 'Cancelled'),
    )

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='role_settlements')
    stage = models.CharField(max_length=30, choices=STAGE_CHOICES)
    distributor = models.ForeignKey(
        'distributors.Distributor', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='payable_settlements'
    )
    stockist = models.ForeignKey(
        'stockist.Stockist', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='receivable_settlements'
    )
    supplier = models.ForeignKey(
        Supplier, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='settlements'
    )
    amount_due = models.DecimalField(max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='pending')
    transfer_reference = models.CharField(max_length=100, blank=True)
    transfer_note = models.CharField(max_length=200, blank=True)
    query_note = models.CharField(max_length=200, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    submitted_by = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='submitted_settlements'
    )
    confirmed_by = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='confirmed_settlements'
    )
    disputed_by = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='disputed_settlements'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at', '-id']
        unique_together = ('order', 'stage', 'supplier')

    def __str__(self):
        return f'Order #{self.order_id} {self.get_stage_display()} - Rs.{self.amount_due}'

    def payer_role(self):
        return 'distributor' if self.stage == 'distributor_to_stockist' else 'stockist'

    def payee_role(self):
        return 'stockist' if self.stage == 'distributor_to_stockist' else 'supplier'

    def payer_name(self):
        if self.stage == 'distributor_to_stockist' and self.distributor:
            return self.distributor.company_name
        if self.stage == 'stockist_to_supplier' and self.stockist:
            return self.stockist.company_name
        return ''

    def payee_name(self):
        if self.stage == 'distributor_to_stockist' and self.stockist:
            return self.stockist.company_name
        if self.stage == 'stockist_to_supplier' and self.supplier:
            return self.supplier.company_name
        return ''


class Commission(models.Model):
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='commissions')
    order_item = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name='commission')
    rule_type = models.CharField(max_length=20, default='supplier_default')
    commission_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    fixed_amount = models.DecimalField(max_digits=8, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])

    def __str__(self):
        return f'Commission for {self.supplier.company_name} — ₹{self.amount}'


def get_order_ledger_status(order):
    if order.status == 'cancelled':
        return 'reversed'

    payment = getattr(order, 'payment', None)
    if payment and payment.status == 'paid':
        return 'settled'

    if order.status in ('at_distributor', 'out_for_delivery', 'partially_delivered', 'delivered'):
        return 'accrued'

    return 'pending'


class FinancialAuditLog(models.Model):
    """Immutable audit trail for all payment and order status changes."""
    ACTION_CHOICES = (
        ('payment_status_changed', 'Payment Status Changed'),
        ('order_status_changed', 'Order Status Changed'),
        ('commission_changed', 'Commission Rule Changed'),
        ('settlement_status_changed', 'Settlement Status Changed'),
        ('manual_adjustment', 'Manual Adjustment'),
    )
    action = models.CharField(max_length=40, choices=ACTION_CHOICES)
    actor = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='financial_audit_logs'
    )
    object_type = models.CharField(max_length=50)
    object_id = models.PositiveIntegerField()
    old_value = models.JSONField(default=dict, blank=True)
    new_value = models.JSONField(default=dict, blank=True)
    note = models.CharField(max_length=300, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['object_type', 'object_id']),
            models.Index(fields=['actor', 'timestamp']),
        ]

    def __str__(self):
        return f'[{self.timestamp:%Y-%m-%d %H:%M}] {self.action} {self.object_type}#{self.object_id}'

    @classmethod
    def log(cls, action, actor, obj, old_value, new_value, ip_address=None, note=''):
        """Convenience method to create an audit log entry."""
        try:
            cls.objects.create(
                action=action,
                actor=actor,
                object_type=type(obj).__name__,
                object_id=obj.pk,
                old_value=old_value if isinstance(old_value, dict) else {'value': str(old_value)},
                new_value=new_value if isinstance(new_value, dict) else {'value': str(new_value)},
                ip_address=ip_address,
                note=note,
            )
        except Exception:
            pass  # Never let logging break a business transaction


def _get_default_stockist():
    from stockist.models import Stockist

    return Stockist.objects.filter(status='active').order_by('id').first()


def sync_order_ledger(order):
    payment = getattr(order, 'payment', None)
    items = list(order.items.select_related('variant', 'product'))

    customer_status = 'reversed' if order.status == 'cancelled' else ('settled' if payment and payment.status == 'paid' else 'pending')
    earning_status = 'reversed' if order.status == 'cancelled' else (
        'settled' if order.status == 'delivered' and payment and payment.status == 'paid'
        else 'accrued' if order.status in ('at_distributor', 'out_for_delivery', 'partially_delivered', 'delivered')
        else 'pending'
    )

    def upsert_entry(*, party_role, entry_type, amount, status, distributor=None, stockist=None, note=''):
        amount = Decimal(str(amount or 0)).quantize(Decimal('0.01'))
        if amount <= 0:
            PaymentLedgerEntry.objects.filter(
                order=order,
                party_role=party_role,
                entry_type=entry_type,
            ).delete()
            return

        PaymentLedgerEntry.objects.update_or_create(
            order=order,
            party_role=party_role,
            entry_type=entry_type,
            defaults={
                'payment': payment,
                'distributor': distributor,
                'stockist': stockist,
                'amount': amount,
                'status': status,
                'note': note,
            },
        )

    upsert_entry(
        party_role='customer',
        entry_type='customer_payment',
        amount=order.total_amount,
        status=customer_status,
        note='Order total payable by customer',
    )

    distributor = order.distributor
    if distributor:
        distributor_margin = sum((item.distributor_margin() for item in items), Decimal('0'))
        upsert_entry(
            party_role='distributor',
            entry_type='distributor_margin',
            amount=distributor_margin,
            status=earning_status,
            distributor=distributor,
            note='Distributor product margin on this order',
        )
        upsert_entry(
            party_role='distributor',
            entry_type='distributor_delivery_income',
            amount=order.delivery_charge,
            status=earning_status,
            distributor=distributor,
            note='Distributor delivery income on this order',
        )
    else:
        PaymentLedgerEntry.objects.filter(order=order, party_role='distributor').delete()

    stockist_charge = Decimal('0')
    if order.fulfillment_source == 'supplier':
        for item in items:
            supplier_price = item.supplier_price
            if supplier_price <= 0 and item.variant_id:
                supplier_price = item.variant.selling_price
            unit_charge = max(item.base_price - supplier_price, Decimal('0'))
            stockist_charge += unit_charge * item.quantity

    stockist = _get_default_stockist() if stockist_charge > 0 else None
    upsert_entry(
        party_role='stockist',
        entry_type='stockist_processing_charge',
        amount=stockist_charge,
        status=earning_status,
        stockist=stockist,
        note='Stockist processing and handling charge on supplier-fulfilled order',
    )


def sync_order_settlements(order):
    items = list(order.items.select_related('product__supplier', 'variant'))
    default_stockist = _get_default_stockist()

    def upsert_settlement(*, stage, amount_due, supplier=None):
        amount_due = Decimal(str(amount_due or 0)).quantize(Decimal('0.01'))
        existing = RoleSettlement.objects.filter(order=order, stage=stage, supplier=supplier)

        if amount_due <= 0 or order.status == 'cancelled':
            if existing.exists():
                existing.exclude(status='confirmed').update(status='cancelled', amount_due=amount_due)
            return

        defaults = {
            'distributor': order.distributor,
            'stockist': default_stockist,
            'amount_due': amount_due,
        }
        if existing.exists():
            settlement = existing.first()
            update_fields = ['distributor', 'stockist', 'amount_due']
            settlement.distributor = order.distributor
            settlement.stockist = default_stockist
            settlement.amount_due = amount_due
            if settlement.status == 'cancelled':
                settlement.status = 'pending'
                update_fields.append('status')
            settlement.save(update_fields=update_fields)
        else:
            RoleSettlement.objects.create(
                order=order,
                stage=stage,
                supplier=supplier,
                distributor=order.distributor,
                stockist=default_stockist,
                amount_due=amount_due,
            )

    if order.fulfillment_source != 'supplier' or not default_stockist:
        RoleSettlement.objects.filter(order=order).exclude(status='confirmed').update(status='cancelled')
        return

    # Determine if the distributor is also a supplier (they self-supply some products).
    # If the distributor has a supplier_profile, any item whose product.supplier.user
    # matches the distributor's user is self-supplied — skip stockist involvement.
    distributor_supplier_pk = None
    if order.distributor_id:
        try:
            dist_user = order.distributor.user
            distributor_supplier_pk = dist_user.supplier_profile.pk
        except Exception:
            distributor_supplier_pk = None

    # Separate items: stockist-sourced vs self-supplied by distributor
    stockist_items = []
    for item in items:
        supplier = item.product.supplier if item.product_id else None
        if distributor_supplier_pk and supplier and supplier.pk == distributor_supplier_pk:
            # Distributor owns this product — no stockist needed
            continue
        stockist_items.append(item)

    distributor_due = sum((item.base_subtotal() for item in stockist_items), Decimal('0'))
    upsert_settlement(stage='distributor_to_stockist', amount_due=distributor_due)

    supplier_totals = {}
    for item in stockist_items:
        supplier = item.product.supplier if item.product_id else None
        if not supplier:
            continue
        supplier_totals.setdefault(supplier.pk, {'supplier': supplier, 'amount': Decimal('0')})
        supplier_totals[supplier.pk]['amount'] += (item.supplier_price or Decimal('0')) * item.quantity

    active_supplier_ids = []
    for row in supplier_totals.values():
        active_supplier_ids.append(row['supplier'].pk)
        upsert_settlement(
            stage='stockist_to_supplier',
            amount_due=row['amount'],
            supplier=row['supplier'],
        )

    RoleSettlement.objects.filter(
        order=order,
        stage='stockist_to_supplier'
    ).exclude(
        supplier_id__in=active_supplier_ids
    ).exclude(status='confirmed').update(status='cancelled')
