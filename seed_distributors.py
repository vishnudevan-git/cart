"""
Creates 3 distributors with customers and links products + prices.
Run: python seed_distributors.py
"""
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'lakrish_mart.settings')
django.setup()

from accounts.models import User
from distributors.models import Distributor, DistributorProduct, DistributorVariantPrice
from products.models import Product, ProductVariant

# ── Distributor data ──────────────────────────────────────────────────────────
DISTRIBUTORS = [
    {
        'username': 'dist_anna',
        'password': 'dist123',
        'first_name': 'Annamalai',
        'phone': '9876500001',
        'email': 'anna@lakrish.com',
        'company_name': 'Anna Nagar Organics',
        'area': 'Anna Nagar, Chennai',
        'delivery_charge': 30,
        'free_delivery_above': 500,
        'default_margin_percent': 12,
        'admin_margin_percent': 8,   # admin charges this % above supplier price
        'customers': [
            {'username': 'ravi_anna', 'first_name': 'Ravi', 'phone': '9876511001'},
            {'username': 'priya_anna', 'first_name': 'Priya', 'phone': '9876511002'},
            {'username': 'kumar_anna', 'first_name': 'Kumar', 'phone': '9876511003'},
        ],
    },
    {
        'username': 'dist_velachery',
        'password': 'dist123',
        'first_name': 'Velmurugan',
        'phone': '9876500002',
        'email': 'vel@lakrish.com',
        'company_name': 'Velachery Fresh Store',
        'area': 'Velachery, Chennai',
        'delivery_charge': 25,
        'free_delivery_above': 400,
        'default_margin_percent': 10,
        'admin_margin_percent': 8,
        'customers': [
            {'username': 'sundar_vel', 'first_name': 'Sundar', 'phone': '9876522001'},
            {'username': 'meena_vel', 'first_name': 'Meena', 'phone': '9876522002'},
        ],
    },
    {
        'username': 'dist_tambaram',
        'password': 'dist123',
        'first_name': 'Tamizharasan',
        'phone': '9876500003',
        'email': 'tam@lakrish.com',
        'company_name': 'Tambaram Organic Hub',
        'area': 'Tambaram, Chennai',
        'delivery_charge': 20,
        'free_delivery_above': 350,
        'default_margin_percent': 15,
        'admin_margin_percent': 8,
        'customers': [
            {'username': 'raja_tam', 'first_name': 'Raja', 'phone': '9876533001'},
            {'username': 'kavitha_tam', 'first_name': 'Kavitha', 'phone': '9876533002'},
            {'username': 'senthil_tam', 'first_name': 'Senthil', 'phone': '9876533003'},
        ],
    },
]

products = list(Product.objects.filter(status='approved').prefetch_related('variants'))

for d_data in DISTRIBUTORS:
    # Create user
    if User.objects.filter(username=d_data['username']).exists():
        user = User.objects.get(username=d_data['username'])
        print(f"Distributor exists: {d_data['username']}")
    else:
        user = User.objects.create_user(
            username=d_data['username'],
            password=d_data['password'],
            first_name=d_data['first_name'],
            phone=d_data['phone'],
            email=d_data['email'],
            role='distributor',
        )

    # Create distributor profile
    dist, created = Distributor.objects.get_or_create(
        user=user,
        defaults={
            'company_name': d_data['company_name'],
            'area': d_data['area'],
            'delivery_charge': d_data['delivery_charge'],
            'free_delivery_above': d_data['free_delivery_above'],
            'default_margin_percent': d_data['default_margin_percent'],
            'status': 'active',
        }
    )
    if created:
        print(f"Created distributor: {dist.company_name} | Code: {dist.invite_code}")
    else:
        print(f"Distributor exists: {dist.company_name} | Code: {dist.invite_code}")

    # Create customers linked to this distributor
    for c in d_data['customers']:
        if not User.objects.filter(username=c['username']).exists():
            cust = User.objects.create_user(
                username=c['username'],
                password='cust123',
                first_name=c['first_name'],
                phone=c['phone'],
                email=f"{c['username']}@lakrish.com",
                role='customer',
                distributor=dist,
            )
            print(f"  Customer: {cust.username}")
        else:
            print(f"  Customer exists: {c['username']}")

    # List all products for this distributor with prices
    admin_margin = d_data['admin_margin_percent'] / 100
    dist_margin = d_data['default_margin_percent'] / 100

    for product in products:
        dp, _ = DistributorProduct.objects.get_or_create(
            distributor=dist, product=product,
            defaults={'is_listed': True, 'margin_percent': d_data['default_margin_percent']}
        )
        dp.is_listed = True
        dp.save()

        for variant in product.variants.filter(is_active=True):
            supplier_price = float(variant.selling_price)
            base_price = round(supplier_price * (1 + admin_margin), 2)
            selling_price = round(base_price * (1 + dist_margin), 2)

            DistributorVariantPrice.objects.update_or_create(
                distributor=dist, variant=variant,
                defaults={
                    'base_price': base_price,
                    'selling_price': selling_price,
                    'is_active': True,
                }
            )

    print(f"  → {products.__len__()} products listed with prices\n")

print("Done. Distributor seed complete.")
print("\nLogin credentials:")
print("  Distributors: dist_anna / dist_velachery / dist_tambaram  |  password: dist123")
print("  Customers: ravi_anna / priya_anna / sundar_vel / meena_vel / raja_tam / kavitha_tam / senthil_tam  |  password: cust123")
