from django.contrib import admin
from .models import Distributor, DistributorProduct, DistributorVariantPrice, Wholesaler


@admin.register(Wholesaler)
class WholesalerAdmin(admin.ModelAdmin):
    """Shown in Django admin as 'Distributor (wholesale)'. Create these, then
    assign Marketplaces to them via the Marketplace's 'wholesaler' field below."""
    list_display = ['company_name', 'area', 'status', 'default_margin_percent', 'enterprise_count']
    list_filter = ['status']
    search_fields = ['company_name', 'user__username']


@admin.register(Distributor)
class DistributorAdmin(admin.ModelAdmin):
    # 'Distributor' here is the code model = UI "Marketplace".
    list_display = ['company_name', 'area', 'status', 'wholesaler', 'invite_code', 'delivery_charge']
    list_filter = ['status', 'wholesaler']
    list_editable = ['wholesaler']  # quick assign an Marketplace to a wholesale Distributor
    search_fields = ['company_name', 'invite_code']


@admin.register(DistributorProduct)
class DistributorProductAdmin(admin.ModelAdmin):
    list_display = ['distributor', 'product', 'is_listed', 'margin_percent']


@admin.register(DistributorVariantPrice)
class DistributorVariantPriceAdmin(admin.ModelAdmin):
    list_display = ['distributor', 'variant', 'base_price', 'selling_price', 'is_active']
