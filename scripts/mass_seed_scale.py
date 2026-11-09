"""DESTRUCTIVE local scale-fixture generator.

This script permanently clears non-superuser users and all categories before it
creates a large synthetic data set. It is blocked unless explicitly run against
the demo database; never use it on the normal development or production DB.

PowerShell:
  $env:LAKRISH_DB_MODE='demo'
  $env:LAKRISH_ALLOW_DESTRUCTIVE_SEED='YES'
  venv\Scripts\python.exe scripts\mass_seed_scale.py
"""
import os, sys, django, random, string
from decimal import Decimal
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'lakrish_mart.settings')
django.setup()

from django.utils import timezone
from accounts.models import User
from suppliers.models import Supplier
from products.models import Product, ProductVariant, Category, VariantPriceHistory
from orders.models import Order, OrderItem
from payments.models import Payment, Commission
from distributors.models import Distributor, DistributorProduct, DistributorVariantPrice
from delivery.models import Delivery


def require_destructive_demo_mode():
    if (os.environ.get('LAKRISH_DB_MODE', '').strip().lower() != 'demo' or
            os.environ.get('LAKRISH_ALLOW_DESTRUCTIVE_SEED') != 'YES'):
        raise SystemExit(
            'REFUSED: mass_seed_scale permanently deletes data. Set '
            "LAKRISH_DB_MODE=demo and LAKRISH_ALLOW_DESTRUCTIVE_SEED=YES "
            'only for a disposable demo database.'
        )


require_destructive_demo_mode()

print("1. Wiping entire database to clean slate (retaining admin)...")
User.objects.exclude(is_superuser=True).delete()
Category.objects.all().delete()

if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser('admin', 'admin@lakrish.local', 'admin123')

print("2. Generating Categories...")
base_categories = [
    'Organic Essentials', 'Spices & Herbs', 'Dry Fruits', 'Grains & Pulses', 'Tea & Coffee', 
    'Cold Pressed Oils', 'Snacks', 'Sweets', 'Dairy', 'Health Supplements', 
    'Personal Care', 'Home Essentials', 'Stationery', 'Beverages', 'Bakery'
]
categories = []
for name in base_categories:
    cat = Category.objects.create(name=name, slug=name.lower().replace(' ', '-').replace('&', 'and'))
    categories.append(cat)

print("3. Generating 200 Suppliers...")
suppliers = []
for i in range(1, 201):
    uname = f'supplier_{i}'
    user = User.objects.create_user(
        username=uname, password='password123', role='supplier', roles='supplier',
        first_name=f'Supplier', last_name=str(i), phone=f'80000{str(i).zfill(5)}'
    )
    sup = Supplier.objects.create(
        user=user, company_name=f'Mega Supply Co {i}', category=random.choice(base_categories).lower(),
        status='approved'
    )
    suppliers.append(sup)

print("4. Generating 200 Distributors...")
distributors = []
for i in range(1, 201):
    uname = f'distributor_{i}'
    user = User.objects.create_user(
        username=uname, password='password123', role='distributor', roles='distributor',
        first_name=f'Distributor', last_name=str(i), phone=f'70000{str(i).zfill(5)}'
    )
    dist = Distributor.objects.create(
        user=user, company_name=f'Local Hub {i}', area=f'Zone {i%10}',
        status='active', default_margin_percent=Decimal(str(random.randint(10, 25))),
        delivery_charge=Decimal(str(random.randint(30, 80))), phone=user.phone
    )
    distributors.append(dist)

print("5. Generating 200 Customers...")
customers = []
for i in range(1, 201):
    uname = f'customer_{i}'
    dist = random.choice(distributors)
    user = User.objects.create_user(
        username=uname, password='password123', role='customer', roles='customer',
        first_name=f'Customer', last_name=str(i), phone=f'90000{str(i).zfill(5)}', distributor=dist
    )
    customers.append(user)

print("6. Generating 50 Additional Suppliers...")
for i in range(1, 51):
    uname = f'additional_supplier_{i}'
    user = User.objects.create_user(
        username=uname, password='password123', role='supplier', roles='supplier',
        first_name='Additional', last_name=f'Supplier {i}', phone=f'60000{str(i).zfill(5)}'
    )
    sup = Supplier.objects.create(
        user=user, company_name=f'Additional Supply Co {i}', category='mixed',
        status='approved'
    )
    suppliers.append(sup)

print("7. Generating Products (1500+ variants)...")
approved_products = []
for sup in suppliers:
    # Each supplier gets 3-5 products
    for p_idx in range(random.randint(3, 5)):
        prod = Product.objects.create(
            supplier=sup, category=random.choice(categories),
            name=f'Premium {random.choice(["Grade A", "Export", "Organic", "Fresh", "Raw"])} Item {sup.pk}-{p_idx}',
            description='Dummy rich description generated for scale testing.',
            status='approved', rating=Decimal(str(round(random.uniform(3.5, 5.0), 1)))
        )
        approved_products.append(prod)
        for v_idx in ['250g', '500g', '1kg']:
            base_price = random.randint(50, 500)
            ProductVariant.objects.create(
                product=prod, label=v_idx,
                mrp=Decimal(str(base_price + 50)),
                selling_price=Decimal(str(base_price)),
                stock=random.randint(50, 500)
            )

print("8. Linking Products to Distributors...")
# Since n=250 dists x 1000 prods is 250,000 links, we'll randomize a subset for speed
for dist in distributors:
    dist_catalog = random.sample(approved_products, 50)
    dp_objs = []
    dvp_objs = []
    for prod in dist_catalog:
        dp_objs.append(DistributorProduct(distributor=dist, product=prod, is_listed=True, margin_percent=dist.default_margin_percent))
        for v in prod.variants.all():
            base_price = v.selling_price * Decimal('1.05')
            selling = base_price * (1 + (dist.default_margin_percent/100))
            dvp_objs.append(DistributorVariantPrice(
                distributor=dist, variant=v, base_price=round(base_price, 2), selling_price=round(selling, 2), is_active=True
            ))
    DistributorProduct.objects.bulk_create(dp_objs)
    DistributorVariantPrice.objects.bulk_create(dvp_objs)

print("9. Generating 1000 Orders (Mix and Match)...")
now = timezone.now()
STATUSES = ['pending', 'confirmed', 'at_distributor', 'out_for_delivery', 'delivered', 'cancelled']

for i in range(1000):
    cust = random.choice(customers)
    dist = cust.distributor
    if not dist: continue
    
    # Pick random products from distributor's catalog
    dvps = list(DistributorVariantPrice.objects.filter(distributor=dist).select_related('variant', 'variant__product'))
    if not dvps: continue
    
    selected_dvps = random.sample(dvps, random.randint(1, 4))
    
    subtotal = sum(d.selling_price * random.randint(1, 3) for d in selected_dvps)
    del_charge = Decimal('0') if subtotal > dist.free_delivery_above else dist.delivery_charge
    total = subtotal + del_charge
    
    days_ago = random.randint(0, 60)
    created_at = now - timedelta(days=days_ago)
    status = random.choice(STATUSES)
    
    order = Order.objects.create(
        customer=cust, distributor=dist, subtotal=subtotal, delivery_charge=del_charge,
        total_amount=total, status=status, address=f'{random.randint(10, 999)} Main Street', phone=cust.phone or '0000'
    )
    Order.objects.filter(pk=order.pk).update(created_at=created_at)
    
    for dvp in selected_dvps:
        qty = random.randint(1, 3)
        oi = OrderItem.objects.create(
            order=order, product=dvp.variant.product, variant=dvp.variant, variant_label=dvp.variant.label,
            quantity=qty, price=dvp.selling_price, base_price=dvp.base_price, mrp=dvp.variant.mrp
        )
        if status == 'delivered':
            # Commission snapshot — use global 10% rate as a default since
            # commission_rate field was removed from Supplier.
            rate = Decimal('10.00')
            Commission.objects.create(
                supplier=dvp.variant.product.supplier, order_item=oi,
                rule_type='default', commission_rate=rate, amount=round(oi.base_subtotal() * rate / 100, 2)
            )

    pay_status = 'paid' if status == 'delivered' else 'pending'
    Payment.objects.create(order=order, amount=total, payment_method='cod', status=pay_status)

print("SUCCESS: 3-Role Mass Seed completely successfully!")
