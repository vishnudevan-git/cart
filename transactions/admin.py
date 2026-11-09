from django.contrib import admin
from .models import ProductTransaction, RoleTransaction, OrderTransaction, OrderItemTransaction, VariantTransaction


@admin.register(ProductTransaction)
class ProductTransactionAdmin(admin.ModelAdmin):
    list_display = ('product', 'event', 'initiated_by', 'created_at')
    list_filter = ('event', 'created_at')
    search_fields = ('product__name', 'initiated_by__username')
    readonly_fields = ('created_at',)


@admin.register(RoleTransaction)
class RoleTransactionAdmin(admin.ModelAdmin):
    list_display = ('user', 'event', 'role_affected', 'initiated_by', 'created_at')
    list_filter = ('event', 'created_at')
    search_fields = ('user__username', 'role_affected')
    readonly_fields = ('created_at',)


@admin.register(OrderTransaction)
class OrderTransactionAdmin(admin.ModelAdmin):
    list_display = ('order', 'event', 'old_status', 'new_status', 'initiated_by', 'created_at')
    list_filter = ('event', 'created_at')
    search_fields = ('order__id', 'initiated_by__username')
    readonly_fields = ('created_at',)


@admin.register(OrderItemTransaction)
class OrderItemTransactionAdmin(admin.ModelAdmin):
    list_display = ('item', 'event', 'initiated_by', 'created_at')
    list_filter = ('event', 'created_at')
    search_fields = ('item__order__id', 'initiated_by__username')
    readonly_fields = ('created_at',)


@admin.register(VariantTransaction)
class VariantTransactionAdmin(admin.ModelAdmin):
    list_display = ('variant', 'event', 'initiated_by', 'created_at')
    list_filter = ('event', 'created_at')
    search_fields = ('variant__product__name', 'initiated_by__username')
    readonly_fields = ('created_at',)
