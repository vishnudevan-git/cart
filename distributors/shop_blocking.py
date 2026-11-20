"""Distributor-to-shop blocking (kb/13).

When a shop request waits at its Distributor (status awaiting_stock) and the
Distributor has enough free packs, those packs are blocked at once, so no other shop
or customer can take them. The block is released when the Distributor fills the
request from its stock, or when it forwards the request to its Stockist (the
Stockist then blocks its own packs).
"""
from django.db import transaction
from django.db.models import F, Value
from django.db.models.functions import Greatest


def block_for_shop_request(req):
    """Block the Distributor's free packs for this shop request. Returns True if blocked."""
    from distributors.models import RestockRequest
    from distributors.wholesaler_models import WholesalerStock

    w = getattr(req.distributor, 'wholesaler', None)
    if (w is None or req.loose_grams or req.status != 'awaiting_stock'
            or req.wholesaler_blocked):
        return False
    with transaction.atomic():
        row = WholesalerStock.objects.select_for_update().filter(
            wholesaler=w, variant=req.variant).first()
        if row is None or row.free_quantity < req.quantity_requested:
            return False
        WholesalerStock.objects.filter(pk=row.pk).update(
            blocked_quantity=F('blocked_quantity') + req.quantity_requested)
        RestockRequest.objects.filter(pk=req.pk).update(wholesaler_blocked=True)
        req.wholesaler_blocked = True
    return True


def release_shop_request_block(req):
    """Give back the Distributor's packs held for this shop request, if it holds any."""
    from distributors.models import RestockRequest
    from distributors.wholesaler_models import WholesalerStock

    if not req.wholesaler_blocked:
        return
    w = getattr(req.distributor, 'wholesaler', None)
    if w is not None:
        WholesalerStock.objects.filter(wholesaler=w, variant=req.variant).update(
            blocked_quantity=Greatest(F('blocked_quantity') - req.quantity_requested, Value(0)))
    RestockRequest.objects.filter(pk=req.pk).update(wholesaler_blocked=False)
    req.wholesaler_blocked = False
