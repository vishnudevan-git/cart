from django.contrib import admin
from .models import Payment, Commission, PaymentLedgerEntry, RoleSettlement

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ['order', 'amount', 'payment_method', 'status', 'transaction_reference', 'paid_at']

@admin.register(Commission)
class CommissionAdmin(admin.ModelAdmin):
    list_display = ['supplier', 'commission_rate', 'amount']


@admin.register(PaymentLedgerEntry)
class PaymentLedgerEntryAdmin(admin.ModelAdmin):
    list_display = ['order', 'party_role', 'entry_type', 'amount', 'status']
    list_filter = ['party_role', 'entry_type', 'status']


@admin.register(RoleSettlement)
class RoleSettlementAdmin(admin.ModelAdmin):
    list_display = ['order', 'stage', 'amount_due', 'amount_paid', 'status', 'transfer_reference']
    list_filter = ['stage', 'status']
