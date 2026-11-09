from django.contrib import admin
from .models import Wallet, WalletTransaction, RentalItem, RentalBlock, RentalBooking


@admin.register(RentalItem)
class RentalItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'distributor', 'owner_kind', 'status', 'rent_per_day', 'item_cost', 'units', 'is_deleted')
    list_filter = ('status', 'owner_kind', 'category', 'is_deleted')
    search_fields = ('name',)


@admin.register(RentalBooking)
class RentalBookingAdmin(admin.ModelAdmin):
    list_display = ('id', 'item', 'renter', 'start_day', 'days', 'amount', 'status')
    list_filter = ('status', 'renter_kind')


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ('customer', 'distributor', 'balance', 'updated_at')
    search_fields = ('customer__username',)


admin.site.register(WalletTransaction)
admin.site.register(RentalBlock)
