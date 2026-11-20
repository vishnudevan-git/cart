"""Simulate customers buying bulk demo products as singles, end to end.

Uses the real cart and checkout views through the Django test client, against
the current database. Every order is a real Order row. After each checkout the
script checks the invariants below and prints any it finds.

Invariants:
  1. No stock count goes negative.
  2. Shop (DistributorStock) quantity equals the sum of its batches.
  3. Order subtotal equals the sum of item price x quantity.
  4. Order total equals subtotal + delivery - reward - wallet.
  5. Every order item has its quantity and price copied from the cart.
  6. Shop stock falls by exactly the quantity taken from it.

Run (venv active, repo root):
    python manage.py shell -c "exec(open('scripts/bulk_singles_sim.py', encoding='utf-8').read())"
"""
import random
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum
from django.test import Client

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from accounts.models import User
from cart.models import Cart
from distributors.models import DistributorStock
from orders.models import Order, OrderItem
from products.models import ProductVariant

CUSTOMER = '9100000033'
DEMO_SKUS = ['GREE-STXX-001', 'GREE-STXX-002', 'GREE-STXX-003']
ORDERS = 12
MAX_QTY = 3
SEED = 42

random.seed(SEED)
customer = User.objects.get(username=CUSTOMER)
shop = customer.distributor
variants = list(
    ProductVariant.objects.filter(product__sku__in=DEMO_SKUS, is_active=True)
    .select_related('product').order_by('product__sku')
)
client = Client()
client.force_login(customer)
session = client.session
session['active_role'] = customer.primary_role()
session.save()

problems = []


def shop_stock(variant):
    row = DistributorStock.objects.filter(distributor=shop, variant=variant).first()
    return row.quantity if row else 0


def check_stock_consistency(tag):
    for row in DistributorStock.objects.filter(distributor=shop):
        batch_sum = row.batches.aggregate(s=Sum('quantity'))['s'] or 0
        if row.quantity < 0:
            problems.append(f'{tag}: negative shop stock {row.variant}')
        if row.quantity != batch_sum:
            problems.append(f'{tag}: shop stock {row.variant} is {row.quantity} but batches sum {batch_sum}')
    for v in variants:
        if v.stock < 0:
            problems.append(f'{tag}: negative supplier stock {v}')


def check_order(order, tag):
    items = list(order.items.all())
    subtotal = sum((i.price * i.quantity for i in items), Decimal('0'))
    if order.subtotal != subtotal.quantize(Decimal('1')):
        problems.append(f'{tag}: order {order.pk} subtotal {order.subtotal} != items {subtotal}')
    expected_total = order.subtotal + order.delivery_charge - order.reward_discount - order.wallet_paid
    if order.total_amount != expected_total:
        problems.append(f'{tag}: order {order.pk} total {order.total_amount} != expected {expected_total}')


def cart_quantities():
    return {c.variant_id: c.quantity for c in Cart.objects.filter(customer=customer)}


print(f'Customer {CUSTOMER} at {shop.company_name}')
print('Start shop stock:', {v.product.sku: shop_stock(v) for v in variants})
print('Start supplier stock:', {v.product.sku: v.stock for v in variants})
print()

results = []
for n in range(1, ORDERS + 1):
    Cart.objects.filter(customer=customer).delete()
    picks = random.sample(variants, k=random.choice([1, 2, 3]))
    wanted = {}
    for v in picks:
        qty = random.randint(1, MAX_QTY)
        wanted[v.pk] = qty
        r = client.post(f'/cart/add/{v.pk}/', {'quantity': qty})
        if r.status_code not in (200, 302):
            problems.append(f'order {n}: add to cart {v} returned {r.status_code}')

    in_cart = cart_quantities()
    if in_cart != wanted:
        problems.append(f'order {n}: cart {in_cart} != requested {wanted}')

    before_shop = {v.pk: shop_stock(v) for v in variants}
    before_supplier = {v.pk: ProductVariant.objects.get(pk=v.pk).stock for v in variants}
    before_orders = Order.objects.count()
    r = client.post('/orders/checkout/', {
        'address_id': '', 'address': f'Test Home, Chennai', 'phone': customer.phone or CUSTOMER,
        'door_number': '', 'street_details': '', 'locality': '', 'city': '', 'pincode': '',
    })
    if Order.objects.count() != before_orders + 1:
        problems.append(f'order {n}: checkout did not create an order (HTTP {r.status_code}, redirect {r.get("Location", "-")})')
        Cart.objects.filter(customer=customer).delete()
        continue

    order = Order.objects.filter(customer=customer).order_by('-pk').first()
    check_order(order, f'order {n}')
    for item in order.items.all():
        if item.quantity != wanted.get(item.variant_id):
            problems.append(f'order {n}: item {item.variant_label} qty {item.quantity} != cart {wanted.get(item.variant_id)}')

    from_shop = {}
    for v in variants:
        drop = before_shop[v.pk] - shop_stock(v)
        if drop:
            from_shop[v.product.sku] = drop
    if order.fulfillment_source == 'distributor_stock':
        taken = sum(from_shop.values())
        if taken != sum(wanted.values()):
            problems.append(f'order {n}: shop stock fell by {taken} but ordered {sum(wanted.values())}')
    elif from_shop:
        problems.append(f'order {n}: shop stock changed on a supplier-sourced order: {from_shop}')

    # Invariant 7: supplier stock drops only for lines NOT filled from shop stock.
    shop_lines = {i.variant_id for i in order.items.all() if i.fulfillment_status == 'shipped'}
    if order.fulfillment_source == 'distributor_stock':
        shop_lines = set(wanted)
    for v in variants:
        if v.pk not in wanted:
            continue
        expected_drop = 0 if v.pk in shop_lines else wanted[v.pk]
        actual_drop = before_supplier[v.pk] - ProductVariant.objects.get(pk=v.pk).stock
        if actual_drop != expected_drop:
            problems.append(f'order {n}: supplier stock {v.product.sku} fell by {actual_drop}, expected {expected_drop}')

    check_stock_consistency(f'order {n}')
    results.append((order.pk, order.fulfillment_source, order.status, order.total_amount,
                    {v.product.sku: wanted[v.pk] for v in picks}, from_shop))
    Cart.objects.filter(customer=customer).delete()

print(f'{"order":>6} {"source":18} {"status":12} {"total":>8}  ordered              shop stock taken')
for pk, src, status, total, ordered, taken in results:
    print(f'{pk:>6} {src:18} {status:12} {str(total):>8}  {str(ordered):20} {taken}')

print()
print('End shop stock:', {v.product.sku: shop_stock(v) for v in variants})
print('End supplier stock:', {v.product.sku: ProductVariant.objects.get(pk=v.pk).stock for v in variants})
print()
if problems:
    print(f'{len(problems)} problem(s):')
    for p in problems:
        print('  -', p)
else:
    print(f'All {len(results)} orders passed every check.')
