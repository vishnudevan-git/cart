"""Pre-create the supplier's shipment record the moment an order line exists.

Why a signal: the supplier Orders page used to create these rows (one query per
line) on every page view. Creating them here means the page is a pure read and
it works no matter which code path creates the order line (checkout, seed
scripts, tests).

The row is a snapshot: target comes from the product, mode from the supplier's
EFFECTIVE mode (global switch wins over the product setting).
"""
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import OrderItem, SupplierShipment


@receiver(post_save, sender=OrderItem)
def create_supplier_shipment(sender, instance, created, **kwargs):
    if not created or kwargs.get('raw'):
        return
    product = instance.product
    supplier = getattr(product, 'supplier', None) if product else None
    if supplier is None:
        return  # product deleted / no supplier — nothing for a supplier to ship
    SupplierShipment.objects.get_or_create(
        order_item=instance,
        defaults={
            'supplier': supplier,
            'target': getattr(product, 'delivery_target', 'stockist') or 'stockist',
            'mode': supplier.effective_shipment_mode(product),
            'status': 'awaiting',
        },
    )
