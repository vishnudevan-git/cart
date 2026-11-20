"""Create bulk test orders on the CURRENT database (test data only).

Places COD orders with large quantities of 1 kg packs for existing customers,
using the same pricing and ledger sync as scripts/full_test_seed.py.
Stock is NOT deducted, matching the existing seed scripts.

Run from the repo root (PowerShell, venv active):
    python manage.py shell -c "exec(open('scripts/bulk_test_orders.py').read())"
"""
from decimal import Decimal
from django.db import transaction
from accounts.models import User
from distributors.models import DistributorVariantPrice
from orders.models import Order, OrderItem
from payments.models import Payment, sync_order_ledger, sync_order_settlements
from products.models import ProductVariant


def place_bulk_order(customer, distributor, lines):
    """lines = list of (variant_pk, quantity). Quantity is in packs."""
    delivery = Decimal(distributor.delivery_charge or 0)
    order = Order.objects.create(
        customer=customer,
        distributor=distributor,
        fulfillment_source='supplier',
        status='confirmed',
        address=f'{customer.first_name or customer.username} Home, Chennai',
        phone=customer.phone or customer.username,
        delivery_charge=delivery,
        subtotal=Decimal('0'),
        total_amount=Decimal('0'),
        notes='BULK TEST ORDER',
    )
    subtotal = Decimal('0')
    for variant_pk, qty in lines:
        variant = ProductVariant.objects.select_related('product').get(pk=variant_pk)
        dvp = DistributorVariantPrice.objects.filter(
            distributor=distributor, variant=variant
        ).first()
        price = dvp.selling_price if dvp else variant.selling_price
        base = dvp.base_price if dvp else variant.selling_price
        OrderItem.objects.create(
            order=order,
            product=variant.product,
            variant=variant,
            variant_label=variant.label,
            quantity=qty,
            price=price,
            base_price=base,
            supplier_price=variant.selling_price,
            mrp=variant.mrp or 0,
        )
        subtotal += Decimal(price) * qty
    order.subtotal = subtotal
    order.total_amount = subtotal + delivery
    order.save(update_fields=['subtotal', 'total_amount'])
    Payment.objects.create(
        order=order, amount=order.total_amount, payment_method='cod', status='pending',
    )
    sync_order_ledger(order)
    sync_order_settlements(order)
    return order


BULK_PLAN = [
    # (customer username, distributor pk, [(variant pk, packs), ...])
    ('9100000020', 6, [(324, 10), (353, 5)]),   # Premium Almonds 1kg x10, Cashews 1kg x5
    ('9100000033', 6, [(357, 25)]),              # Cashews 1kg x25 (bulk)
    ('9100000034', 6, [(326, 5)]),               # Organic Ghee 1 litre x5
    ('9711010200', 10, [(339, 12)]),             # Premium Almonds 1kg x12 at North Chennai
]

with transaction.atomic():
    for username, dist_pk, lines in BULK_PLAN:
        customer = User.objects.get(username=username)
        from distributors.models import Distributor
        distributor = Distributor.objects.get(pk=dist_pk)
        order = place_bulk_order(customer, distributor, lines)
        print(f'Order #{order.pk}: {customer.username} @ {distributor} '
              f'items={order.items.count()} total=Rs.{order.total_amount}')
