from django.db import models
from django.core.exceptions import ValidationError
from django.conf import settings
from decimal import Decimal, ROUND_HALF_UP
from products.models import Product, ProductVariant


def _round_rupee(value):
    return Decimal(str(value or 0)).quantize(Decimal('1'), rounding=ROUND_HALF_UP)


class Order(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
        ('stockist_picked', 'Picked by Stockist from Supplier'),
        ('at_distributor', 'At Distributor'),
        ('out_for_delivery', 'Out for Delivery'),
        ('partially_delivered', 'Partially Delivered'),
        ('delivered', 'Delivered'),
        ('cancelled', 'Cancelled'),
    )

    # Define which transitions are allowed. Any status can transition to cancelled.
    VALID_TRANSITIONS = {
        'pending': {'confirmed', 'at_distributor', 'cancelled'},
        'confirmed': {'stockist_picked', 'at_distributor', 'cancelled'},
        'stockist_picked': {'at_distributor', 'cancelled'},
        'at_distributor': {'out_for_delivery', 'cancelled'},
        'out_for_delivery': {'delivered', 'partially_delivered', 'cancelled'},
        'partially_delivered': {'delivered', 'cancelled'},
        'delivered': set(),
        'cancelled': set(),
    }

    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='orders'
    )
    distributor = models.ForeignKey(
        'distributors.Distributor', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='orders'
    )
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    delivery_charge = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    reward_discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    wallet_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    address = models.TextField()
    phone = models.CharField(max_length=15)
    notes = models.TextField(blank=True)
    # 'distributor_stock' = distributor has it ready, 'supplier' = needs to be picked from supplier
    fulfillment_source = models.CharField(
        max_length=20,
        choices=(('supplier', 'From Supplier'), ('distributor_stock', 'From Distributor Stock')),
        default='supplier'
    )
    is_partial_shipment = models.BooleanField(
        default=False,
        help_text='True when only some items were available and shipped immediately.'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Order #{self.id} by {self.customer.username}'

    def can_transition_to(self, new_status):
        """Return True if a status change to new_status is valid."""
        return new_status in self.VALID_TRANSITIONS.get(self.status, set())

    def transition_status(self, new_status, updated_by=None):
        """
        Update order status through the validated state machine.
        Raises ValidationError if the transition is not permitted.
        Call save() after this to persist the change.
        """
        if not self.can_transition_to(new_status):
            raise ValidationError(
                f"Cannot move order #{self.pk} from '{self.status}' to '{new_status}'. "
                f"Allowed next states: {sorted(self.VALID_TRANSITIONS.get(self.status, set()))}"
            )
        self.status = new_status

    def save(self, *args, **kwargs):
        self.subtotal = _round_rupee(self.subtotal)
        self.delivery_charge = _round_rupee(self.delivery_charge)
        self.total_amount = _round_rupee(self.total_amount)
        super().save(*args, **kwargs)


class OrderItem(models.Model):
    FULFILLMENT_CHOICES = (
        ('pending', 'Pending'),
        ('shipped', 'Shipped'),
        ('backordered', 'Back-ordered'),
        ('delivered', 'Delivered'),
        ('cancelled', 'Cancelled'),
    )
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    variant = models.ForeignKey(ProductVariant, on_delete=models.SET_NULL, null=True)
    variant_label = models.CharField(max_length=50, blank=True)
    quantity = models.PositiveIntegerField()
    # Price customer paid (distributor's selling price)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    # Base price (stockist price = supplier price + processing charges)
    base_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    # Supplier sell price frozen at checkout so stockist charge remains auditable later.
    supplier_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    mrp = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    fulfillment_status = models.CharField(
        max_length=15, choices=FULFILLMENT_CHOICES, default='pending',
        help_text='Per-item fulfillment state for partial shipments.'
    )
    # Tracks which COD collection batch delivered this item.
    delivery_instalment = models.ForeignKey(
        'payments.PaymentInstalment', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='items',
        help_text='The COD instalment in which this item was delivered and paid for.'
    )

    def subtotal(self):
        return self.price * self.quantity

    def base_subtotal(self):
        return self.base_price * self.quantity

    def distributor_margin(self):
        return (self.price - self.base_price) * self.quantity

    def stockist_charge(self):
        return max(self.base_price - self.supplier_price, 0) * self.quantity

    def save(self, *args, **kwargs):
        self.price = _round_rupee(self.price)
        self.base_price = _round_rupee(self.base_price)
        self.supplier_price = _round_rupee(self.supplier_price)
        self.mrp = _round_rupee(self.mrp)
        super().save(*args, **kwargs)



class SupplierShipment(models.Model):
    """Per-order-item shipment record owned by the SUPPLIER.

    One row per supplier order line. It records whether the supplier has
    confirmed/prepared the line, the level they ship it to (copied from the
    product at creation so later product edits don't rewrite history), the
    mode (collect vs send), and — for 'send' — the transhipment details.

    Downstream roles read this to know a line is ready/dispatched. Skipped
    levels (any tier before `target`) never see the order at all; this record
    is the source of truth for which level is the FIRST to see it.
    """
    TARGET_CHOICES = (
        ('stockist', 'Stockist'),
        ('distributor', 'Distributor'),
        ('shop', 'Shop / Marketplace'),
        ('customer', 'Customer'),
    )
    MODE_CHOICES = (
        ('collect', 'Collected by target'),
        ('send', 'Sent by supplier'),
    )
    # NOTE: the stored key 'confirmed' means "packed and READY" (to dispatch for
    # send mode, for pickup for collect mode). The key is kept so existing rows
    # and history need no data migration; only the label changed.
    STATUS_CHOICES = (
        ('awaiting', 'Awaiting supplier'),      # order placed, supplier not yet packed
        ('delayed', 'Delayed'),                 # supplier announced a delay (still to be packed)
        ('confirmed', 'Ready'),                 # packed: ready to dispatch / ready for pickup
        ('dispatched', 'Dispatched'),           # supplier sent it (send mode)
        ('received', 'Received by target'),     # target level acknowledged receipt
        ('unavailable', 'Unavailable'),         # supplier cannot supply this line
        ('cancelled', 'Cancelled'),
    )

    # Why a line is delayed / unavailable. The reason + expected date are shown
    # to the customer and receivers; the free-text note is PRIVATE (supplier + admin).
    DELAY_REASONS = (
        ('production', 'Production delay'),
        ('waiting_stock', 'Waiting for stock'),
        ('quality_check', 'Quality check'),
        ('transport', 'Transport issue'),
        ('other', 'Other'),
    )
    UNAVAILABLE_REASONS = (
        ('out_of_stock', 'Out of stock'),
        ('discontinued', 'Discontinued'),
        ('quality_rejected', 'Quality rejected'),
        ('unavail_other', 'Other'),
    )
    ISSUE_REASON_CHOICES = DELAY_REASONS + UNAVAILABLE_REASONS

    order_item = models.OneToOneField(
        'orders.OrderItem', on_delete=models.CASCADE, related_name='supplier_shipment'
    )
    # Denormalised for fast per-supplier queries and stable history.
    supplier = models.ForeignKey(
        'suppliers.Supplier', on_delete=models.CASCADE, related_name='shipments'
    )
    target = models.CharField(max_length=12, choices=TARGET_CHOICES, default='stockist')
    mode = models.CharField(max_length=8, choices=MODE_CHOICES, default='collect')
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='awaiting')

    # Transhipment details — only meaningful for mode='send'.
    dispatched_to_name = models.CharField(
        max_length=200, blank=True,
        help_text='Who/where it was dispatched to (contact or address).'
    )
    carrier = models.CharField(
        max_length=120, blank=True, help_text='Courier / transport / vehicle.'
    )
    tracking_reference = models.CharField(
        max_length=120, blank=True, help_text='Tracking number / LR / docket.'
    )
    transhipment_note = models.TextField(blank=True)

    # Self-dispatch (mode='send') cost policy: the RECEIVER bears the pickup /
    # freight. They enter the actual amount later from their own login and fold
    # it into their margin. We only store it here for the record — it does NOT
    # change the order price automatically.
    receiver_pickup_charge = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text='Pickup/freight the receiver paid for a self-dispatched line. '
                  'Entered by the receiver; informational, not auto-priced.'
    )

    # Delay / unavailable details (only meaningful while status is 'delayed' or
    # 'unavailable'). Flag only: nothing here changes the order total, a refund
    # or the ledger - the shop/admin decides what to do about an unavailable line.
    issue_reason = models.CharField(
        max_length=20, choices=ISSUE_REASON_CHOICES, blank=True,
        help_text='Reason category. Visible to the customer and receivers.'
    )
    issue_note = models.TextField(
        blank=True, help_text='PRIVATE note: visible to the supplier and admin only.'
    )
    expected_ready_on = models.DateField(
        null=True, blank=True, help_text='When the supplier expects to have it ready.'
    )
    issue_at = models.DateTimeField(null=True, blank=True)
    # Set when the supplier says an UNAVAILABLE line can be supplied again after
    # the undo window. The line stays 'unavailable' until the shop confirms the
    # customer still wants it (the supplier cannot know that).
    resume_requested_at = models.DateTimeField(null=True, blank=True)

    # A supplier can undo "unavailable" on their own only this soon after
    # marking it (accidental click). Later it needs the shop's confirmation.
    UNAVAILABLE_UNDO_MINUTES = 120

    confirmed_at = models.DateTimeField(null=True, blank=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['supplier', 'status']),
            models.Index(fields=['target', 'status']),
        ]

    def __str__(self):
        return f'Shipment for item #{self.order_item_id} → {self.target} ({self.status})'

    def clear_issue(self):
        """Forget any delay/unavailable details (caller saves)."""
        self.issue_reason = ''
        self.issue_note = ''
        self.expected_ready_on = None
        self.issue_at = None
        self.resume_requested_at = None

    @property
    def within_undo_window(self):
        """True while the supplier may reverse 'unavailable' without the shop."""
        if self.issue_at is None:
            return True
        from datetime import timedelta
        from django.utils import timezone
        return timezone.now() - self.issue_at <= timedelta(minutes=self.UNAVAILABLE_UNDO_MINUTES)

    @property
    def public_stage(self):
        """(label, badge css, icon) safe to show the customer and receivers.

        Never includes the private note or the supplier's carrier details.
        """
        s = self.status
        if s == 'delayed':
            label = 'Delayed'
            if self.expected_ready_on:
                label += f' · expected {self.expected_ready_on:%d %b}'
            return label, 'bg-warning text-dark', 'bi-clock-history'
        if s == 'confirmed':
            label = ('Packed, ready for pickup' if self.mode == 'collect'
                     else 'Packed, ready to dispatch')
            return label, 'bg-success', 'bi-box-seam'
        if s == 'dispatched':
            return 'Dispatched by supplier', 'bg-info text-dark', 'bi-truck'
        if s == 'received':
            return 'Received', 'bg-dark', 'bi-check2-all'
        if s == 'unavailable':
            if self.resume_requested_at:
                return ('Supplier can supply again, awaiting shop confirmation',
                        'bg-info text-dark', 'bi-hourglass-split')
            return 'Unavailable', 'bg-danger', 'bi-x-circle'
        if s == 'cancelled':
            return 'Cancelled', 'bg-secondary', 'bi-slash-circle'
        return 'Being prepared', 'bg-secondary', 'bi-hourglass'

    @property
    def public_reason(self):
        """Reason text for delayed/unavailable lines, else ''."""
        if self.status in ('delayed', 'unavailable') and self.issue_reason:
            return self.get_issue_reason_display()
        return ''
