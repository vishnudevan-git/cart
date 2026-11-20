"""Stock returned to the party that gave it to the customer.

Only the customer can cancel or reduce an order item, and only while the order is
pending. The returned packs go back to whoever supplied them for that item:
- shop items go back to the shop's (distributor's) stock;
- supplier items go back to the supplier's stock.
Nothing else returns stock.
"""
from django.db.models import F


def return_item_stock(order, item, quantity):
    """Put ``quantity`` packs of ``item`` back where they came from."""
    if quantity <= 0 or item.variant_id is None:
        return

    from lakrish_mart.activity_logger import act
    act.stock(f'returned_to_{item.stock_source}', variant=item.variant, qty=quantity, by=order.customer, detail=f'order #{order.pk}, cancelled or reduced by customer')
    if item.stock_source in ('wholesaler', 'stockist'):
        from orders.upstream_stock import return_upper
        return_upper(item.stock_source, order.distributor, item.variant, quantity)
        return

    if item.stock_source == 'shop':
        act.stock('returned_to_shop', variant=item.variant, qty=quantity, by=order.customer, detail=f'order #{order.pk}')
        from distributors.models import DistributorStock, DistributorStockBatch
        stock_obj, _ = DistributorStock.objects.select_for_update().get_or_create(
            distributor=order.distributor, variant=item.variant, defaults={'quantity': 0},
        )
        DistributorStockBatch.objects.create(
            stock=stock_obj, quantity=quantity, cost_price=item.base_price,
            note=f'Returned from order #{order.pk}',
        )
        stock_obj.refresh_quantity()
        return

    from products.models import Product, ProductVariant
    variant = item.variant
    product = Product.objects.select_for_update().get(pk=variant.product_id)
    if variant.unit_grams and product.master_stock_grams:
        # Checkout took these packs out of the product's loose pool. Put the grams back,
        # then recompute every pack from the pool, the same way checkout did.
        product.master_stock_grams = product.master_stock_grams + quantity * variant.unit_grams
        product.save(update_fields=['master_stock_grams'])
        product.sync_variant_stocks()
    else:
        ProductVariant.objects.filter(pk=variant.pk).update(stock=F('stock') + quantity)
