"""DESTRUCTIVE local fixture generator.

This script permanently clears non-superuser users and all categories before
creating random fixture data and downloading placeholder images. It is blocked
unless explicitly run against a disposable demo database.

PowerShell:
  $env:LAKRISH_DB_MODE='demo'
  $env:LAKRISH_ALLOW_DESTRUCTIVE_SEED='YES'
  venv\Scripts\python.exe scripts\minimal_seed.py
"""
import os, sys, django, random, string
from decimal import Decimal
import urllib.request
from django.core.files.base import ContentFile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'lakrish_mart.settings')
django.setup()

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
            'REFUSED: minimal_seed permanently deletes data. Set '
            "LAKRISH_DB_MODE=demo and LAKRISH_ALLOW_DESTRUCTIVE_SEED=YES "
            'only for a disposable demo database.'
        )


require_destructive_demo_mode()


def fetch_image(width=600, height=400):
    url = f"https://picsum.photos/{width}/{height}?random={random.randint(1, 10000)}"
    try:
        response = urllib.request.urlopen(url)
        if response.status == 200:
            return response.read()
    except Exception as e:
        print(f"Failed to fetch image: {e}")
    return None

print("1. Wiping entire database to clean slate (retaining admin)...")
User.objects.exclude(is_superuser=True).delete()
Category.objects.all().delete()

if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser('admin', 'admin@lakrish.local', 'admin123')

print("2. Generating Categories (with pictures)...")
base_categories = [
    'Organic Essentials', 'Spices & Herbs', 'Dry Fruits', 'Grains & Pulses', 'Tea & Coffee'
]
categories = []
for name in base_categories:
    cat = Category(name=name, slug=name.lower().replace(' ', '-').replace('&', 'and'))
    img_content = fetch_image(400, 300)
    if img_content:
        cat.image.save(f'{cat.slug}.jpg', ContentFile(img_content), save=False)
    cat.save()
    categories.append(cat)

print("3. Generating 10 Suppliers...")
suppliers = []
for i in range(1, 11):
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

print("4. Generating 10 Distributors...")
distributors = []
for i in range(1, 11):
    uname = f'distributor_{i}'
    user = User.objects.create_user(
        username=uname, password='password123', role='distributor', roles='distributor',
        first_name=f'Distributor', last_name=str(i), phone=f'70000{str(i).zfill(5)}'
    )
    dist = Distributor.objects.create(
        user=user, company_name=f'Local Hub {i}', area=f'Zone {i%5}',
        status='active', default_margin_percent=Decimal(str(random.randint(10, 25))),
        delivery_charge=Decimal(str(random.randint(30, 80))), phone=user.phone
    )
    distributors.append(dist)

print("5. Generating 10 Customers...")
customers = []
for i in range(1, 11):
    uname = f'customer_{i}'
    dist = random.choice(distributors)
    user = User.objects.create_user(
        username=uname, password='password123', role='customer', roles='customer',
        first_name=f'Customer', last_name=str(i), phone=f'90000{str(i).zfill(5)}', distributor=dist
    )
    customers.append(user)

print("6. Generating 100 Products (with pictures)...")
approved_products = []
for p_idx in range(1, 101):
    print(f"   Fetching pic and creating product {p_idx}/100...", end='\r')
    sup = random.choice(suppliers)
    prod = Product(
        supplier=sup, category=random.choice(categories),
        name=f'Premium {random.choice(["Grade A", "Export", "Organic", "Fresh", "Raw"])} Item {p_idx}',
        description='Dummy rich description generated for scale testing.',
        status='approved', rating=Decimal(str(round(random.uniform(3.5, 5.0), 1)))
    )
    img_content = fetch_image(600, 400)
    if img_content:
        prod.image.save(f'product_{p_idx}.jpg', ContentFile(img_content), save=False)
    prod.save()
    approved_products.append(prod)
    for v_idx in ['250g', '500g', '1kg']:
        base_price = random.randint(50, 500)
        ProductVariant.objects.create(
            product=prod, label=v_idx,
            mrp=Decimal(str(base_price + 50)),
            selling_price=Decimal(str(base_price)),
            stock=random.randint(50, 500)
        )
print() # newline after progress bar

print("7. Linking Products to Distributors...")
for dist in distributors:
    # Give each distributor access to all products
    dist_catalog = approved_products
    dp_objs = []
    dvp_objs = []
    
    # Check what already exists to avoid UniqueConstraint errors
    existing_dp_pks = set(DistributorProduct.objects.filter(distributor=dist).values_list('product_id', flat=True))
    
    for prod in dist_catalog:
        if prod.id not in existing_dp_pks:
            dp_objs.append(DistributorProduct(distributor=dist, product=prod, is_listed=True, margin_percent=dist.default_margin_percent))
        
        for v in prod.variants.all():
            base_price = v.selling_price * Decimal('1.05')
            selling = base_price * (1 + (dist.default_margin_percent/100))
            dvp_objs.append(DistributorVariantPrice(
                distributor=dist, variant=v, base_price=round(base_price, 2), selling_price=round(selling, 2), is_active=True
            ))
            
    DistributorProduct.objects.bulk_create(dp_objs, ignore_conflicts=True)
    DistributorVariantPrice.objects.bulk_create(dvp_objs, ignore_conflicts=True)

print("SUCCESS: Mass Seed for minimal testing completely successfully!")
