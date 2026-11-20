"""Nearest-first stock for a customer line (kb/13, owner rule 2026-11-20).

A line is filled from the nearest tier that can cover the whole line:
shop (handled in checkout) -> Distributor (wholesaler) -> Stockist -> Supplier.
The free stock of each tier is used: stock minus blocked. A bundle line is just a
count of packs, so the same rule covers it. If no upper tier covers the line, checkout
falls back to the supplier path.

Returns go back to the tier named in OrderItem.stock_source.
"""
from django.db.models import F
from django.db.models.functions import Greatest
from django.db.models import Value


def _wholesaler(distributor):
    return getattr(distributor, 'wholesaler', None)


def _stockist(distributor):
    from stockist.models import stockist_for
    return stockist_for(distributor)


def tier_free(distributor, variant, tier):
    """Free packs of ``variant`` at an upper tier ('wholesaler' or 'stockist')."""
    if tier == 'wholesaler':
        from distributors.wholesaler_models import WholesalerStock
        w = _wholesaler(distributor)
        if w is None:
            return 0
        row = WholesalerStock.objects.filter(wholesaler=w, variant=variant).first()
        return row.free_quantity if row else 0
    if tier == 'stockist':
        from stockist.models import StockistInventory
        st = _stockist(distributor)
        if st is None:
            return 0
        row = StockistInventory.objects.filter(stockist=st, variant=variant).first()
        return row.free_quantity if row else 0
    return 0


def pick_upper_tier(distributor, variant, quantity):
    """Nearest upper tier that can cover the whole line: 'wholesaler', 'stockist' or None.

    None means the supplier path decides (checkout checks the supplier's free stock).
    """
    for tier in ('wholesaler', 'stockist'):
        if tier_free(distributor, variant, tier) >= quantity:
            return tier
    return None


def consume_upper(tier, distributor, variant, quantity):
    """Take ``quantity`` packs from an upper tier. Caller has checked the free stock."""
    if tier == 'wholesaler':
        from distributors.wholesaler_models import WholesalerStock
        WholesalerStock.objects.filter(wholesaler=_wholesaler(distributor), variant=variant).update(
            quantity=F('quantity') - quantity)
    elif tier == 'stockist':
        from stockist.models import StockistInventory
        StockistInventory.objects.filter(stockist=_stockist(distributor), variant=variant).update(
            quantity=F('quantity') - quantity)


def return_upper(tier, distributor, variant, quantity):
    """Put packs back at the upper tier that gave them (order cancelled or reduced)."""
    if tier == 'wholesaler':
        from distributors.wholesaler_models import WholesalerStock
        row, _ = WholesalerStock.objects.get_or_create(
            wholesaler=_wholesaler(distributor), variant=variant, defaults={'quantity': 0})
        WholesalerStock.objects.filter(pk=row.pk).update(quantity=F('quantity') + quantity)
    elif tier == 'stockist':
        from stockist.models import StockistInventory
        row, _ = StockistInventory.objects.get_or_create(
            stockist=_stockist(distributor), variant=variant, defaults={'quantity': 0})
        StockistInventory.objects.filter(pk=row.pk).update(quantity=F('quantity') + quantity)


def chain_free_for(distributor, variant):
    """Free packs a customer of this shop can still reach above the shop.

    Used by the customer page: a product with nothing free above the shop and nothing
    on the shop's own shelf is hidden.
    """
    total = 0
    for tier in ('wholesaler', 'stockist'):
        total += tier_free(distributor, variant, tier)
    try:
        total += variant.available_stock
    except AttributeError:
        pass
    return total
