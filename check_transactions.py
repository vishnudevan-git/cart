#!/usr/bin/env python
"""Check transactions for issues."""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'lakrish_mart.settings')
django.setup()

from orders.models import Order, OrderItem
from payments.models import RoleSettlement

print('=== Transaction Checks ===')
print()

# Check orders
orders = Order.objects.all()
print(f'Total orders: {orders.count()}')
print(f'  Pending: {orders.filter(status="pending").count()}')
print(f'  Confirmed: {orders.filter(status="confirmed").count()}')
print(f'  Delivered: {orders.filter(status="delivered").count()}')
print(f'  Cancelled: {orders.filter(status="cancelled").count()}')
print()

# Check order items
order_items = OrderItem.objects.all()
print(f'Total order items: {order_items.count()}')
print()

# Check settlements
settlements = RoleSettlement.objects.all()
print(f'Total settlements: {settlements.count()}')
print(f'  Pending: {settlements.filter(status="pending").count()}')
print(f'  Confirmed: {settlements.filter(status="confirmed").count()}')
print(f'  Disputed: {settlements.filter(status="disputed").count()}')
print()

# Check for orders without settlements
orders_without_settlements = orders.filter(status='delivered').exclude(id__in=settlements.values('order_id'))
print(f'Delivered orders without settlements: {orders_without_settlements.count()}')

# Check for duplicate settlements
from django.db.models import Count
duplicate_settlements = settlements.values('order_id', 'stage').annotate(
    count=Count('id')
).filter(count__gt=1)
print(f'\\nDuplicate settlements: {duplicate_settlements.count()}')
for dup in duplicate_settlements:
    print(f'  order_id={dup["order_id"]}, stage={dup["stage"]}, count={dup["count"]}')

# Check for disputed settlements
disputed = settlements.filter(status='disputed')
print(f'\\nDisputed settlements: {disputed.count()}')
for d in disputed[:5]:
    print(f'  Order: {d.order_id}, Stage: {d.stage}, Amount: {d.amount}')
