"""
Procurement views — Stockist ↔ Supplier billing ledger (NOT customer-order-based).

URL prefix: /stockist/procurement/
  GET/POST /stockist/procurement/                    → procurement_center  (account list + invoice/payment creation)
  GET      /stockist/procurement/account/<pk>/       → account_detail (invoices, payments, allocations)
  POST     /stockist/procurement/invoice/create/     → create_invoice
  POST     /stockist/procurement/payment/create/     → create_payment
  POST     /stockist/procurement/invoice/<pk>/cancel/ → cancel_invoice
"""

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from suppliers.models import Supplier

from .models import Stockist
from .procurement_models import (
    ProcurementInvoice,
    ProcurementInvoiceItem,
    ProcurementPayment,
    StockistSupplierAccount,
    get_or_create_account,
)
from .views import stockist_required


@stockist_required
def procurement_center(request):
    """
    Stockist's main procurement screen.
    Shows all supplier accounts, and forms to add an invoice or record a payment.
    """
    stockist = request.user.stockist_profile
    suppliers = Supplier.objects.filter(status='approved').order_by('company_name')

    # Accounts the stockist has transacted with, plus balance
    accounts = StockistSupplierAccount.objects.filter(
        stockist=stockist
    ).select_related('supplier').order_by('supplier__company_name')

    # Summary
    total_due = accounts.aggregate(t=Sum('balance_due'))['t'] or Decimal('0')
    total_credit = accounts.aggregate(t=Sum('excess_credit'))['t'] or Decimal('0')

    return render(request, 'stockist/procurement/center.html', {
        'stockist': stockist,
        'accounts': accounts,
        'suppliers': suppliers,
        'total_due': total_due,
        'total_credit': total_credit,
    })


@stockist_required
def account_detail(request, pk):
    """
    Full timeline for one stockist ↔ supplier account:
    all invoices, all payments, all allocations.
    """
    stockist = request.user.stockist_profile
    account = get_object_or_404(StockistSupplierAccount, pk=pk, stockist=stockist)
    invoices = account.invoices.prefetch_related('allocations__payment').order_by('invoice_date', 'id')
    payments = account.payments.prefetch_related('allocations__invoice').order_by('-payment_date', '-id')

    return render(request, 'stockist/procurement/account_detail.html', {
        'stockist': stockist,
        'account': account,
        'invoices': invoices,
        'payments': payments,
    })


@stockist_required
def create_invoice(request):
    """POST: Create a new procurement invoice for a supplier."""
    if request.method != 'POST':
        return redirect('stockist:procurement_center')

    stockist = request.user.stockist_profile
    supplier_id = request.POST.get('supplier_id', '').strip()
    try:
        supplier = Supplier.objects.get(pk=supplier_id, status='approved')
    except Supplier.DoesNotExist:
        messages.error(request, 'Invalid supplier.')
        return redirect('stockist:procurement_center')

    raw_amount = request.POST.get('total_amount', '').strip()
    try:
        total_amount = Decimal(raw_amount)
        if total_amount <= 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        messages.error(request, 'Enter a valid positive invoice amount.')
        return redirect('stockist:procurement_center')

    invoice_number = request.POST.get('invoice_number', '').strip()
    invoice_date_raw = request.POST.get('invoice_date', '').strip()
    notes = request.POST.get('notes', '').strip()
    due_date_raw = request.POST.get('due_date', '').strip()

    try:
        invoice_date = timezone.datetime.strptime(invoice_date_raw, '%Y-%m-%d').date() if invoice_date_raw else timezone.localdate()
    except ValueError:
        invoice_date = timezone.localdate()

    due_date = None
    if due_date_raw:
        try:
            due_date = timezone.datetime.strptime(due_date_raw, '%Y-%m-%d').date()
        except ValueError:
            due_date = None

    account = get_or_create_account(stockist, supplier)

    invoice = ProcurementInvoice.objects.create(
        account=account,
        invoice_number=invoice_number,
        invoice_date=invoice_date,
        due_date=due_date,
        total_amount=total_amount,
        notes=notes,
        created_by=request.user,
    )

    # If account already has credit, auto-apply to this new invoice
    if account.excess_credit > 0:
        # Create a synthetic allocation from existing credit (no ProcurementPayment row needed)
        apply = min(account.excess_credit, invoice.balance)
        from .procurement_models import ProcurementPaymentAllocation
        # Find the most recent payment from which the excess originated
        last_payment = account.payments.order_by('-created_at').first()
        if last_payment:
            ProcurementPaymentAllocation.objects.get_or_create(
                payment=last_payment,
                invoice=invoice,
                defaults={'amount_allocated': apply},
            )
            invoice.paid_amount += apply
            invoice.save()
            account.excess_credit -= apply
            account.save(update_fields=['excess_credit'])

    account.refresh_from_invoices()
    messages.success(request, f'Invoice ₹{total_amount} added for {supplier.company_name}.')
    return redirect('stockist:account_detail', pk=account.pk)


@stockist_required
def create_payment(request):
    """POST: Record a payment from stockist to supplier — auto-allocate FIFO."""
    if request.method != 'POST':
        return redirect('stockist:procurement_center')

    stockist = request.user.stockist_profile
    supplier_id = request.POST.get('supplier_id', '').strip()
    try:
        supplier = Supplier.objects.get(pk=supplier_id, status='approved')
    except Supplier.DoesNotExist:
        messages.error(request, 'Invalid supplier.')
        return redirect('stockist:procurement_center')

    raw_amount = request.POST.get('amount', '').strip()
    try:
        amount = Decimal(raw_amount)
        if amount <= 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        messages.error(request, 'Enter a valid positive payment amount.')
        return redirect('stockist:procurement_center')

    method = request.POST.get('payment_method', 'upi')
    reference = request.POST.get('reference', '').strip()
    note = request.POST.get('note', '').strip()
    date_raw = request.POST.get('payment_date', '').strip()
    try:
        payment_date = timezone.datetime.strptime(date_raw, '%Y-%m-%d').date() if date_raw else timezone.localdate()
    except ValueError:
        payment_date = timezone.localdate()

    account = get_or_create_account(stockist, supplier)
    payment = ProcurementPayment.objects.create(
        account=account,
        amount=amount,
        payment_date=payment_date,
        payment_method=method,
        reference=reference,
        note=note,
        recorded_by=request.user,
    )
    payment.auto_allocate()

    if payment.excess_amount > 0:
        messages.success(
            request,
            f'Payment ₹{amount} recorded. All invoices cleared — ₹{payment.excess_amount} saved as credit.'
        )
    else:
        messages.success(request, f'Payment ₹{amount} recorded and allocated to invoices.')

    return redirect('stockist:account_detail', pk=account.pk)


@stockist_required
def cancel_invoice(request, pk):
    """POST: Cancel an open/partial invoice."""
    if request.method != 'POST':
        return redirect('stockist:procurement_center')

    stockist = request.user.stockist_profile
    invoice = get_object_or_404(ProcurementInvoice, pk=pk, account__stockist=stockist)
    if invoice.status == 'paid':
        messages.error(request, 'Cannot cancel a fully paid invoice.')
        return redirect('stockist:account_detail', pk=invoice.account_id)
    invoice.status = 'cancelled'
    invoice.save(update_fields=['status', 'updated_at'])
    invoice.account.refresh_from_invoices()
    messages.success(request, f'Invoice #{invoice.invoice_number or invoice.pk} cancelled.')
    return redirect('stockist:account_detail', pk=invoice.account_id)


# ── Supplier-side view: see what each stockist owes them ─────────────────────

def supplier_procurement_accounts(request):
    """
    For the supplier role: view procurement accounts where they are the supplier.
    Called from payments:center or supplier nav.  No @stockist_required decorator.
    """
    if not request.user.is_authenticated:
        return redirect('accounts:login')
    try:
        supplier = request.user.supplier_profile
    except Exception:
        return redirect('accounts:login')

    accounts = StockistSupplierAccount.objects.filter(
        supplier=supplier
    ).select_related('stockist').order_by('stockist__company_name')

    total_receivable = accounts.aggregate(t=Sum('balance_due'))['t'] or Decimal('0')

    return render(request, 'suppliers/procurement_accounts.html', {
        'supplier': supplier,
        'accounts': accounts,
        'total_receivable': total_receivable,
    })
