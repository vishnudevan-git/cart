"""
Stage 7g — auto-restock for every stock-holding tier.

When a tier's stock falls to its threshold, the system files the restock
REQUEST that tier would have filed by hand:

    Stockist     → StockistRestockRequest   (fulfilled by the supplier)
    Distributor  → WholesalerRestockRequest (delivered by ITS one stockist)
    Marketplace  → RestockRequest           (stockist — or the supplier for
                                             direct-supply products)

It never touches a quantity directly, so the Stage 7 rule holds: stock still
only INCREASES when a request is delivered. One pending request per variant —
checks are idempotent and safe to run as often as you like.

Called after every stock-reducing action, and by the nightly sweep:

    python3 manage.py auto_restock
"""

AUTO_NOTE = 'Auto-restock (low stock)'



def _skipped(row, max_price):
    """True when this row must NOT be auto-ordered.

    * ``auto_restock_exempt`` — the owner switched this one item off.
    * ``max_price`` — the owner capped auto orders by supplier price, so
      high-value items are left for a deliberate manual order.
    """
    if row.auto_restock_exempt:
        return True
    if max_price and row.variant.selling_price > max_price:
        return True
    return False

def check_stockist(stockist):
    """File supplier requests for this stockist's low inventory rows."""
    from .models import StockistInventory, StockistRestockRequest

    if not stockist.auto_restock_enabled or stockist.status != 'active':
        return 0

    low_rows = StockistInventory.objects.filter(
        stockist=stockist,
        quantity__lte=stockist.auto_restock_threshold,
        variant__is_active=True,
        variant__product__is_deleted=False,
        variant__product__status='approved',
        variant__product__direct_supply=False,       # never passes through us
        variant__product__owner_enterprise__isnull=True,
    ).select_related('variant')

    created = 0
    for row in low_rows:
        if _skipped(row, stockist.auto_restock_max_price):
            continue
        already_open = StockistRestockRequest.objects.filter(
            stockist=stockist, variant=row.variant, status='pending'
        ).exists()
        if already_open:
            continue
        StockistRestockRequest.objects.create(
            stockist=stockist, variant=row.variant,
            quantity_requested=stockist.auto_restock_quantity, note=AUTO_NOTE,
        )
        created += 1
    return created


def check_wholesaler(wholesaler):
    """File stockist requests for this Distributor's low stock rows."""
    from distributors.wholesaler_models import WholesalerStock, WholesalerRestockRequest

    if not wholesaler.auto_restock_enabled or wholesaler.status != 'active':
        return 0
    my_stockist = wholesaler.stockist
    if my_stockist is None or my_stockist.status != 'active':
        return 0                                     # nobody to order from

    low_rows = WholesalerStock.objects.filter(
        wholesaler=wholesaler,
        quantity__lte=wholesaler.auto_restock_threshold,
        variant__is_active=True,
        variant__product__is_deleted=False,
        variant__product__status='approved',
        variant__product__direct_supply=False,
        variant__product__owner_enterprise__isnull=True,
    ).select_related('variant')

    created = 0
    for row in low_rows:
        if _skipped(row, wholesaler.auto_restock_max_price):
            continue
        already_open = WholesalerRestockRequest.objects.filter(
            wholesaler=wholesaler, variant=row.variant, status='pending'
        ).exists()
        if already_open:
            continue
        WholesalerRestockRequest.objects.create(
            wholesaler=wholesaler, stockist=my_stockist,
            variant=row.variant,
            quantity_requested=wholesaler.auto_restock_quantity, note=AUTO_NOTE,
        )
        created += 1
    return created


def check_marketplace(distributor):
    """File chain requests for this shop's low LISTED stock rows.

    Own products are skipped — the shop is their source and sets their stock
    itself. Direct-supply products are included; their request is simply
    fulfilled by the supplier instead of the stockist.
    """
    from distributors.models import DistributorStock, DistributorProduct, RestockRequest

    if not distributor.auto_restock_enabled or distributor.status != 'active':
        return 0

    listed_ids = set(DistributorProduct.objects.filter(
        distributor=distributor, is_listed=True
    ).values_list('product_id', flat=True))

    low_rows = DistributorStock.objects.filter(
        distributor=distributor,
        quantity__lte=distributor.auto_restock_threshold,
        variant__is_active=True,
        variant__product__is_deleted=False,
        variant__product__owner_enterprise__isnull=True,   # never our own goods
    ).select_related('variant')

    created = 0
    for row in low_rows:
        if row.variant.product_id not in listed_ids:
            continue                                 # not selling it any more
        if _skipped(row, distributor.auto_restock_max_price):
            continue
        already_open = RestockRequest.objects.filter(
            distributor=distributor, variant=row.variant,
        ).exclude(status__in=['delivered', 'rejected']).exists()
        if already_open:
            continue
        RestockRequest.objects.create(
            distributor=distributor, variant=row.variant,
            quantity_requested=distributor.auto_restock_quantity, note=AUTO_NOTE,
        )
        created += 1
    return created


def run_all():
    """Sweep every tier (nightly cron / manage.py auto_restock)."""
    from .models import Stockist
    from distributors.models import Distributor
    from distributors.wholesaler_models import Wholesaler

    totals = {'stockist': 0, 'wholesaler': 0, 'marketplace': 0}
    for s in Stockist.objects.filter(status='active', auto_restock_enabled=True):
        totals['stockist'] += check_stockist(s)
    for w in Wholesaler.objects.filter(status='active', auto_restock_enabled=True):
        totals['wholesaler'] += check_wholesaler(w)
    for d in Distributor.objects.filter(status='active', auto_restock_enabled=True):
        totals['marketplace'] += check_marketplace(d)
    return totals
