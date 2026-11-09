from django.contrib import admin
from .procurement_models import (
    StockistSupplierAccount, ProcurementInvoice,
    ProcurementInvoiceItem, ProcurementPayment, ProcurementPaymentAllocation
)


@admin.register(StockistSupplierAccount)
class StockistSupplierAccountAdmin(admin.ModelAdmin):
    list_display = ('stockist', 'supplier', 'total_invoiced', 'total_paid', 'balance_due', 'excess_credit')
    list_filter = ('supplier',)


class InvoiceItemInline(admin.TabularInline):
    model = ProcurementInvoiceItem
    extra = 0


class AllocationInline(admin.TabularInline):
    model = ProcurementPaymentAllocation
    extra = 0
    readonly_fields = ('payment', 'amount_allocated', 'allocated_at')


@admin.register(ProcurementInvoice)
class ProcurementInvoiceAdmin(admin.ModelAdmin):
    list_display = ('id', 'invoice_number', 'account', 'invoice_date', 'total_amount', 'paid_amount', 'balance', 'status')
    list_filter = ('status', 'account__supplier')
    inlines = [InvoiceItemInline, AllocationInline]


@admin.register(ProcurementPayment)
class ProcurementPaymentAdmin(admin.ModelAdmin):
    list_display = ('id', 'account', 'amount', 'payment_date', 'payment_method', 'allocated_amount', 'excess_amount')
    list_filter = ('payment_method',)
    inlines = [AllocationInline]
