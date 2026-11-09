"""
Procurement / Accounts Payable — Stockist ↔ Supplier
=====================================================
This is completely INDEPENDENT of customer orders.

Flow:
  1. Stockist creates a ProcurementInvoice when they buy stock from a supplier.
  2. They record a ProcurementPayment whenever they actually pay money.
  3. auto_allocate() distributes that payment FIFO across unpaid invoices.
  4. If overpayment: excess goes to StockistSupplierAccount.excess_credit.
  5. On the next invoice, existing credit is applied first before any new payment is needed.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from suppliers.models import Supplier
from .models import Stockist


class StockistSupplierAccount(models.Model):
    """Running financial account between one stockist and one supplier."""
    stockist = models.ForeignKey(Stockist, on_delete=models.CASCADE, related_name='supplier_accounts')
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='stockist_accounts')

    # Cumulative totals (maintained automatically)
    total_invoiced = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # balance_due = total_invoiced - total_paid - excess_credit
    balance_due = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Overpayment carried forward (applies to next invoice automatically)
    excess_credit = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('stockist', 'supplier')

    def __str__(self):
        return f'{self.stockist.company_name} ↔ {self.supplier.company_name} (bal ₹{self.balance_due})'

    def refresh_from_invoices(self):
        """Recompute totals from scratch (call after bulk changes)."""
        from django.db.models import Sum as _Sum
        inv_qs = self.invoices.all()
        self.total_invoiced = inv_qs.aggregate(t=_Sum('total_amount'))['t'] or Decimal('0')
        self.total_paid = self.payments.aggregate(t=_Sum('amount'))['t'] or Decimal('0')
        # Sum of per-invoice paid amounts
        alloc_sum = ProcurementPaymentAllocation.objects.filter(
            invoice__account=self
        ).aggregate(t=_Sum('amount_allocated'))['t'] or Decimal('0')
        unallocated = self.total_paid - alloc_sum
        self.excess_credit = max(Decimal('0'), unallocated)
        self.balance_due = max(Decimal('0'), self.total_invoiced - alloc_sum)
        self.save(update_fields=['total_invoiced', 'total_paid', 'balance_due', 'excess_credit', 'updated_at'])


class ProcurementInvoice(models.Model):
    """
    One purchase invoice from a supplier to a stockist.
    NOT linked to any customer order — this is the stockist's own procurement.
    """
    STATUS_CHOICES = (
        ('open', 'Open'),
        ('partial', 'Partially Paid'),
        ('paid', 'Paid'),
        ('cancelled', 'Cancelled'),
    )

    account = models.ForeignKey(StockistSupplierAccount, on_delete=models.CASCADE, related_name='invoices')
    invoice_number = models.CharField(max_length=50, blank=True, help_text='Supplier invoice reference')
    invoice_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField(null=True, blank=True)

    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='open')
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='created_proc_invoices'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['invoice_date', 'id']  # FIFO allocation uses this order

    def __str__(self):
        return f'Invoice {self.invoice_number or self.id} — ₹{self.total_amount} ({self.status})'

    def save(self, *args, **kwargs):
        # Keep balance in sync
        self.balance = max(Decimal('0'), self.total_amount - self.paid_amount)
        if self.paid_amount <= 0:
            self.status = 'open'
        elif self.balance <= 0:
            self.status = 'paid'
        else:
            self.status = 'partial'
        super().save(*args, **kwargs)

    @property
    def stockist(self):
        return self.account.stockist

    @property
    def supplier(self):
        return self.account.supplier


class ProcurementInvoiceItem(models.Model):
    """Optional line items on a procurement invoice."""
    invoice = models.ForeignKey(ProcurementInvoice, on_delete=models.CASCADE, related_name='items')
    description = models.CharField(max_length=200)
    quantity = models.DecimalField(max_digits=10, decimal_places=3, default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    line_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    def save(self, *args, **kwargs):
        self.line_total = self.quantity * self.unit_price
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.description} × {self.quantity} = ₹{self.line_total}'


class ProcurementPayment(models.Model):
    """
    A lump-sum payment from stockist to supplier.
    One payment can span many invoices; auto_allocate() handles the distribution.
    """
    METHOD_CHOICES = (
        ('cash', 'Cash'),
        ('upi', 'UPI'),
        ('bank_transfer', 'Bank Transfer'),
        ('cheque', 'Cheque'),
        ('other', 'Other'),
    )

    account = models.ForeignKey(StockistSupplierAccount, on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_date = models.DateField(default=timezone.localdate)
    payment_method = models.CharField(max_length=15, choices=METHOD_CHOICES, default='upi')
    reference = models.CharField(max_length=100, blank=True, help_text='UTR / cheque number / receipt no.')
    note = models.CharField(max_length=200, blank=True)
    allocated_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    excess_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='recorded_proc_payments'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-payment_date', '-id']

    def __str__(self):
        return f'Payment ₹{self.amount} on {self.payment_date} ({self.account.supplier.company_name})'

    @transaction.atomic
    def auto_allocate(self):
        """
        Allocate this payment FIFO across all open/partial invoices in the account.
        Any excess is stored as account.excess_credit (and this payment's excess_amount).
        Also applies any existing account excess_credit first.
        """
        remaining = Decimal(str(self.amount))
        account = self.account

        # 1. If account already has excess credit from before, include it
        if account.excess_credit > 0:
            remaining += account.excess_credit
            account.excess_credit = Decimal('0')
            account.save(update_fields=['excess_credit'])

        # 2. FIFO — oldest open invoices first
        unpaid_invoices = ProcurementInvoice.objects.select_for_update().filter(
            account=account,
            status__in=['open', 'partial']
        ).order_by('invoice_date', 'id')

        for invoice in unpaid_invoices:
            if remaining <= 0:
                break
            to_apply = min(remaining, invoice.balance)
            if to_apply <= 0:
                continue

            ProcurementPaymentAllocation.objects.create(
                payment=self,
                invoice=invoice,
                amount_allocated=to_apply,
            )
            invoice.paid_amount += to_apply
            invoice.save()  # triggers balance + status update

            self.allocated_amount += to_apply
            remaining -= to_apply

        # 3. Anything left is excess credit
        self.excess_amount = remaining
        self.save(update_fields=['allocated_amount', 'excess_amount'])

        if remaining > 0:
            account.excess_credit = remaining
            account.save(update_fields=['excess_credit'])

        # 4. Refresh running totals
        account.refresh_from_invoices()


class ProcurementPaymentAllocation(models.Model):
    """Maps a payment to a specific invoice and how much was applied."""
    payment = models.ForeignKey(ProcurementPayment, on_delete=models.CASCADE, related_name='allocations')
    invoice = models.ForeignKey(ProcurementInvoice, on_delete=models.CASCADE, related_name='allocations')
    amount_allocated = models.DecimalField(max_digits=12, decimal_places=2)
    allocated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('payment', 'invoice')

    def __str__(self):
        return f'₹{self.amount_allocated} from Payment #{self.payment_id} → Invoice #{self.invoice_id}'


def get_or_create_account(stockist, supplier):
    account, _ = StockistSupplierAccount.objects.get_or_create(
        stockist=stockist, supplier=supplier
    )
    return account
