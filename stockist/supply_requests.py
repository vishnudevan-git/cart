"""Stockist requests to the supplier.

A request blocks its packs straight away: ProductVariant.blocked_stock goes up by the
requested quantity. The supplier still holds those packs (stock only falls when the
supplier ships), but no other stockist can request them. Rejecting a request releases
the block. Shipping a request releases the block and lowers stock together.
"""
from django.db import transaction
from django.db.models import F, Value
from django.db.models.functions import Greatest


class RequestBlocked(ValueError):
    """Not enough unblocked packs for this request."""

    def __init__(self, available, label):
        super().__init__(f'Only {available} × {label} can be requested now.')
        self.available = available
        self.label = label


def create_supplier_request(stockist, variant, quantity, note='', loose_supply=False):
    """Create a pending request and block its packs. Raises RequestBlocked if short."""
    from products.models import ProductVariant
    from .models import StockistRestockRequest

    with transaction.atomic():
        locked = ProductVariant.objects.select_for_update().get(pk=variant.pk)
        if quantity > locked.available_stock:
            raise RequestBlocked(locked.available_stock, locked.label)
        ProductVariant.objects.filter(pk=variant.pk).update(
            blocked_stock=F('blocked_stock') + quantity)
        return StockistRestockRequest.objects.create(
            stockist=stockist, variant=variant, quantity_requested=quantity,
            note=note, loose_supply=loose_supply,
        )


def create_wholesaler_request(wholesaler, stockist, variant, quantity, **fields):
    """A distributor's request to its stockist. Blocks the stockist's free packs at once.

    Raises RequestBlocked when the stockist has fewer free packs than requested.
    Loose (grams) requests are not blocked here; the delivery check still applies.
    """
    from distributors.wholesaler_models import WholesalerRestockRequest
    from .models import StockistInventory

    with transaction.atomic():
        inv = StockistInventory.objects.select_for_update().filter(
            stockist=stockist, variant=variant).first()
        free = inv.free_quantity if inv else 0
        if quantity > free:
            raise RequestBlocked(free, variant.label)
        StockistInventory.objects.filter(pk=inv.pk).update(
            blocked_quantity=F('blocked_quantity') + quantity)
        return WholesalerRestockRequest.objects.create(
            wholesaler=wholesaler, stockist=stockist, variant=variant,
            quantity_requested=quantity, **fields,
        )


def release_wholesaler_block(stockist, variant_pk, quantity):
    """Give back stockist packs held by a distributor request that was rejected or delivered."""
    from .models import StockistInventory

    StockistInventory.objects.filter(stockist=stockist, variant_id=variant_pk).update(
        blocked_quantity=Greatest(F('blocked_quantity') - quantity, Value(0)))


def release_supplier_block(variant_pk, quantity):
    """Give back packs held by a request that was rejected or shipped."""
    from products.models import ProductVariant

    ProductVariant.objects.filter(pk=variant_pk).update(
        blocked_stock=Greatest(F('blocked_stock') - quantity, Value(0)))
