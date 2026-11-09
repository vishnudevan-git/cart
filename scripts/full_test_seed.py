"""
Full Test Seed — Lakrish Mart
==============================
Creates:
  • 3 Categories  (Dry Fruits, Spices, Organic)
  • 3 Suppliers   (mobile = username = password)
  • 10 Products   with variants and MRP
  • 1 Stockist    with inventory and processing charges
  • 3 Distributors with distributor pricing
  • 3 Customers per distributor
  • 3 Sample orders (one per distributor)

Run from repo root (PowerShell):
    venv\Scripts\python.exe manage.py shell -c "exec(open('scripts/full_test_seed.py').read())"
"""
import os, sys, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'lakrish_mart.settings')

from decimal import Decimal
from django.contrib.auth import get_user_model
from django.db import transaction

User = get_user_model()

# ─── helpers ────────────────────────────────────────────────────────────────

def make_user(mobile, name, role, distributor_profile=None):
    """Create (or get) a user with mobile as username/password."""
    user, created = User.objects.get_or_create(username=mobile)
    if created:
        user.set_password(mobile)
        user.phone = mobile
        first, *rest = name.split()
        user.first_name = first
        user.last_name = ' '.join(rest)
        user.role = role
        if distributor_profile:
            user.distributor = distributor_profile
        user.save()
        print(f'  ✓ Created user  {mobile} / {name}  [{role}]')
    else:
        print(f'  · Exists  {mobile} / {name}  [{role}]')
    return user


# ─── 1. Categories ──────────────────────────────────────────────────────────

print('\n[1] Categories')
from products.models import Category, Product, ProductVariant

cats = {}
for name, slug, code in [
    ('Dry Fruits', 'dry-fruits', 'DF'),
    ('Spices',     'spices',     'SP'),
    ('Organic',    'organic',    'OR'),
]:
    cat, _ = Category.objects.get_or_create(slug=slug, defaults={'name': name, 'code': code})
    cats[slug] = cat
    print(f'  ✓ {name}')


# ─── 2. Suppliers ───────────────────────────────────────────────────────────

print('\n[2] Suppliers')
from suppliers.models import Supplier

supplier_data = [
    dict(mobile='9876543201', name='Arjun Dry Fruits Co', company='Arjun Dry Fruits Co',
         code='AD', category='dry_fruits', address='42 Anna Salai, Chennai'),
    dict(mobile='9876543202', name='Priya Spice World',   company='Priya Spice World',
         code='PS', category='spices',     address='7 MG Road, Bengaluru'),
    dict(mobile='9876543203', name='Krishna Organic Farm', company='Krishna Organic Farm',
         code='KO', category='organic',    address='15 Greenfield, Coimbatore'),
]

suppliers = []
for sd in supplier_data:
    user = make_user(sd['mobile'], sd['name'], 'supplier')
    user.add_role('supplier')
    user.save()
    sup, _ = Supplier.objects.get_or_create(
        user=user,
        defaults=dict(
            company_name=sd['company'], code=sd['code'],
            category=sd['category'], address=sd['address'], status='approved',
        )
    )
    suppliers.append(sup)
    print(f'  ✓ Supplier: {sup.company_name}  [{sd["mobile"]}]')


# ─── 3. Products + Variants ─────────────────────────────────────────────────

print('\n[3] Products + Variants')

PRODUCTS = [
    # --- Dry Fruits (supplier 0) ---
    dict(
        supplier=0, cat='dry-fruits', name='Premium Cashews',
        desc='Crunchy whole cashews from Goa farms, W240 grade.',
        show_mrp=True, loose=False,
        variants=[
            ('250g', 250, Decimal('160'), Decimal('180'), 50),
            ('500g', 500, Decimal('310'), Decimal('350'), 40),
            ('1kg',  1000, Decimal('600'), Decimal('680'), 30),
        ]
    ),
    dict(
        supplier=0, cat='dry-fruits', name='Seedless Raisins',
        desc='Golden seedless raisins, naturally sun-dried.',
        show_mrp=True, loose=True,
        variants=[
            ('200g', 200, Decimal('80'),  Decimal('95'),  60),
            ('500g', 500, Decimal('190'), Decimal('220'), 45),
            ('1kg',  1000, Decimal('370'), Decimal('430'), 25),
        ]
    ),
    dict(
        supplier=0, cat='dry-fruits', name='Almonds (Mamra)',
        desc='Mamra almonds rich in Omega-3. No shell, ready to eat.',
        show_mrp=True, loose=False,
        variants=[
            ('100g', 100, Decimal('120'), Decimal('140'), 40),
            ('250g', 250, Decimal('290'), Decimal('330'), 30),
            ('500g', 500, Decimal('570'), Decimal('640'), 20),
        ]
    ),
    # --- Spices (supplier 1) ---
    dict(
        supplier=1, cat='spices', name='Turmeric Powder',
        desc='Pure Erode turmeric, no artificial colour. 5% curcumin.',
        show_mrp=True, loose=True,
        variants=[
            ('100g', 100, Decimal('25'),  Decimal('30'),  80),
            ('250g', 250, Decimal('60'),  Decimal('72'),  60),
            ('500g', 500, Decimal('115'), Decimal('138'), 40),
        ]
    ),
    dict(
        supplier=1, cat='spices', name='Red Chilli Powder',
        desc='Byadagi variety, deep colour, medium heat.',
        show_mrp=True, loose=True,
        variants=[
            ('100g', 100, Decimal('28'),  Decimal('34'),  70),
            ('250g', 250, Decimal('65'),  Decimal('80'),  55),
            ('500g', 500, Decimal('125'), Decimal('155'), 35),
        ]
    ),
    dict(
        supplier=1, cat='spices', name='Coriander Powder',
        desc='Stone-ground from whole coriander. Fresh aroma guaranteed.',
        show_mrp=False, loose=True,
        variants=[
            ('200g', 200, Decimal('32'),  None,           80),
            ('500g', 500, Decimal('75'),  None,           50),
        ]
    ),
    # --- Organic (supplier 2) ---
    dict(
        supplier=2, cat='organic', name='Cold-Pressed Coconut Oil',
        desc='100% virgin cold-pressed from organic coconuts. No chemicals.',
        show_mrp=True, loose=False,
        variants=[
            ('200ml',  200,  Decimal('95'),  Decimal('115'), 30),
            ('500ml',  500,  Decimal('225'), Decimal('270'), 25),
            ('1 litre',1000, Decimal('440'), Decimal('520'), 15),
        ]
    ),
    dict(
        supplier=2, cat='organic', name='Raw Forest Honey',
        desc='Unprocessed wildflower honey, harvested sustainably.',
        show_mrp=True, loose=False,
        variants=[
            ('250g',  250, Decimal('180'), Decimal('220'), 20),
            ('500g',  500, Decimal('350'), Decimal('420'), 15),
            ('1kg',  1000, Decimal('680'), Decimal('820'), 10),
        ]
    ),
    dict(
        supplier=2, cat='organic', name='Black Rice',
        desc='Anthocyanin-rich heirloom black rice from North-East India.',
        show_mrp=True, loose=True,
        variants=[
            ('500g',  500, Decimal('110'), Decimal('135'), 40),
            ('1kg',  1000, Decimal('210'), Decimal('260'), 30),
        ]
    ),
    dict(
        supplier=2, cat='organic', name='Moringa Leaf Powder',
        desc='Sun-dried organic moringa, 65+ nutrients.',
        show_mrp=True, loose=False,
        variants=[
            ('100g', 100, Decimal('70'),  Decimal('90'),  50),
            ('200g', 200, Decimal('135'), Decimal('170'), 35),
        ]
    ),
]

products = []
for pd in PRODUCTS:
    sup = suppliers[pd['supplier']]
    cat = cats[pd['cat']]
    prod, created = Product.objects.get_or_create(
        supplier=sup, name=pd['name'],
        defaults=dict(
            category=cat, description=pd['desc'],
            show_mrp=pd['show_mrp'], allow_loose_distribution=pd['loose'],
            status='approved', charges_confirmed=True,
            master_unit='g',
        )
    )
    if not created:
        prod.status = 'approved'
        prod.charges_confirmed = True
        prod.show_mrp = pd['show_mrp']
        prod.allow_loose_distribution = pd['loose']
        prod.save(update_fields=['status', 'charges_confirmed', 'show_mrp', 'allow_loose_distribution'])

    # Variants
    for label, unit_grams, sell_price, mrp, stock in pd['variants']:
        v, vc = ProductVariant.objects.get_or_create(
            product=prod, label=label,
            defaults=dict(
                selling_price=sell_price, mrp=mrp,
                stock=stock, unit_grams=unit_grams, is_active=True,
            )
        )
        if not vc:
            v.selling_price = sell_price
            v.mrp = mrp
            v.stock = stock
            v.unit_grams = unit_grams
            v.is_active = True
            v.save(update_fields=['selling_price', 'mrp', 'stock', 'unit_grams', 'is_active'])

    products.append(prod)
    action = 'Created' if created else 'Updated'
    print(f'  ✓ {action}: {prod.name} ({len(pd["variants"])} variants)')


# ─── 4. Stockist ────────────────────────────────────────────────────────────

print('\n[4] Stockist')
from stockist.models import Stockist, StockistInventory, StockistStage, ProcessingCharge

st_user = make_user('9876540001', 'Rajan Stores', 'stockist')
st_user.add_role('stockist')
st_user.save()
stockist, _ = Stockist.objects.get_or_create(
    user=st_user,
    defaults=dict(
        company_name='Rajan Stores', area='T Nagar, Chennai',
        phone='9876540001', address='88 Usman Road, T Nagar, Chennai 600017',
        status='active',
    )
)
print(f'  ✓ Stockist: {stockist.company_name}')

# Inventory — seed all approved variants
all_variants = ProductVariant.objects.filter(
    is_active=True, product__status='approved', product__is_deleted=False
)
for v in all_variants:
    StockistInventory.objects.get_or_create(
        stockist=stockist, variant=v,
        defaults={'quantity': v.stock}
    )
print(f'  ✓ Inventory seeded: {all_variants.count()} variants')

# Processing charge stage
stage, _ = StockistStage.objects.get_or_create(
    stockist=stockist, name='Packing & Handling',
    defaults={'display_in': 'both', 'is_active': True, 'order': 1}
)

# Global processing charge: ₹2 per kg
ProcessingCharge.objects.get_or_create(
    stockist=stockist, scope='global', stage_obj=stage, charge_type='per_kg',
    defaults={'charge_value': Decimal('2.00')}
)
print(f'  ✓ Processing charge: ₹2/kg (global)')


# ─── 5. Distributors + Pricing ──────────────────────────────────────────────

print('\n[5] Distributors')
from distributors.models import Distributor, DistributorProduct, DistributorVariantPrice
from stockist.models import get_stockist_price

distributor_data = [
    dict(mobile='9876541001', name='Senthil Kumar', company='Senthil Mart',
         area='Velachery, Chennai', margin=15),
    dict(mobile='9876541002', name='Meena Traders', company='Meena Traders',
         area='Anna Nagar, Chennai', margin=12),
    dict(mobile='9876541003', name='Raj Enterprises', company='Raj Enterprises',
         area='Coimbatore South', margin=10),
]

distributors = []
for dd in distributor_data:
    d_user = make_user(dd['mobile'], dd['name'], 'distributor')
    dist, _ = Distributor.objects.get_or_create(
        user=d_user,
        defaults=dict(
            company_name=dd['company'], area=dd['area'],
            phone=dd['mobile'], default_margin_percent=Decimal(str(dd['margin'])),
            delivery_charge=Decimal('40'), free_delivery_above=Decimal('500'),
            status='active',
        )
    )
    distributors.append(dist)

    # List all approved products with margin pricing
    for prod in products:
        dp, _ = DistributorProduct.objects.get_or_create(
            distributor=dist, product=prod,
            defaults={'is_listed': True, 'margin_percent': Decimal(str(dd['margin'])), 'margin_mode': 'percent'}
        )
        for v in prod.variants.filter(is_active=True):
            stockist_price, _ = get_stockist_price(prod, v, stockist)
            base_price = stockist_price or v.selling_price
            margin_factor = 1 + Decimal(str(dd['margin'])) / 100
            sell = (base_price * margin_factor).quantize(Decimal('0.01'))
            # MRP cap
            if prod.show_mrp and v.mrp:
                sell = min(sell, v.mrp)
            sell = max(sell, base_price)
            DistributorVariantPrice.objects.update_or_create(
                distributor=dist, variant=v,
                defaults={
                    'base_price': base_price,
                    'selling_price': sell,
                    'is_active': True,
                    'price_stale': False,
                }
            )
    print(f'  ✓ Distributor: {dist.company_name}  [{dd["mobile"]}]  margin={dd["margin"]}%')


# ─── 6. Customers ───────────────────────────────────────────────────────────

print('\n[6] Customers')

customer_data = [
    # Senthil Mart customers
    ('9876542001', 'Lakshmi Devi',    0),
    ('9876542002', 'Murugan Raj',     0),
    ('9876542003', 'Anitha Krishnan', 0),
    # Meena Traders customers
    ('9876542011', 'Sathish Babu',    1),
    ('9876542012', 'Kavitha Selvan',  1),
    ('9876542013', 'Prakash Nathan',  1),
    # Raj Enterprises customers
    ('9876542021', 'Vignesh Raja',    2),
    ('9876542022', 'Devi Priya',      2),
    ('9876542023', 'Arun Sundar',     2),
]

customers = []
for mobile, name, dist_idx in customer_data:
    dist = distributors[dist_idx]
    c_user = make_user(mobile, name, 'customer', distributor_profile=dist)
    c_user.add_role('customer')
    c_user.distributor = dist
    c_user.save()
    customers.append((c_user, dist_idx))
    print(f'  ✓ Customer: {name}  [{mobile}]  → {dist.company_name}')


# ─── 7. Sample Orders ───────────────────────────────────────────────────────

print('\n[7] Sample Orders')
from orders.models import Order, OrderItem
from payments.models import Payment, sync_order_ledger, sync_order_settlements
from django.utils import timezone

def place_order(customer_user, distributor, items):
    """items = list of (product, variant_label, qty)"""
    order = Order.objects.create(
        customer=customer_user,
        distributor=distributor,
        fulfillment_source='supplier',
        status='confirmed',
        address=f'{customer_user.first_name} Home, Chennai',
        phone=customer_user.phone or customer_user.username,
        delivery_charge=distributor.delivery_charge,
        subtotal=Decimal('0'),
        total_amount=Decimal('0'),
    )
    total = Decimal('0')
    for prod, vlabel, qty in items:
        variant = prod.variants.filter(label=vlabel, is_active=True).first()
        if not variant:
            continue
        dvp = DistributorVariantPrice.objects.filter(
            distributor=distributor, variant=variant
        ).first()
        price = dvp.selling_price if dvp else variant.selling_price
        base  = dvp.base_price   if dvp else variant.selling_price
        sup_price = variant.selling_price

        item = OrderItem.objects.create(
            order=order,
            product=prod,
            variant=variant,
            quantity=qty,
            price=price,
            base_price=base,
            supplier_price=sup_price,
        )
        total += price * qty

    total += distributor.delivery_charge
    order.subtotal = total - distributor.delivery_charge
    order.total_amount = total
    order.save(update_fields=['subtotal', 'total_amount'])

    Payment.objects.create(
        order=order,
        amount=total,
        payment_method='cod',
        status='pending',
    )
    sync_order_ledger(order)
    sync_order_settlements(order)
    return order


# Order 1 — Lakshmi Devi from Senthil Mart
cashews   = products[0]
raisins   = products[1]
turmeric  = products[3]
coconut_oil = products[6]

o1 = place_order(
    customers[0][0],  # Lakshmi Devi
    distributors[0],  # Senthil Mart
    [
        (cashews,    '500g', 2),
        (raisins,    '200g', 1),
        (turmeric,   '250g', 1),
    ]
)
print(f'  ✓ Order #{o1.pk} — {customers[0][0].get_full_name()} — ₹{o1.total_amount}')

# Order 2 — Sathish Babu from Meena Traders
honey    = products[7]
almonds  = products[2]

o2 = place_order(
    customers[3][0],   # Sathish Babu
    distributors[1],   # Meena Traders
    [
        (almonds,  '250g',  1),
        (honey,    '500g',  1),
        (coconut_oil, '200ml', 1),
    ]
)
print(f'  ✓ Order #{o2.pk} — {customers[3][0].get_full_name()} — ₹{o2.total_amount}')

# Order 3 — Vignesh Raja from Raj Enterprises
black_rice = products[8]
moringa    = products[9]
chilli     = products[4]

o3 = place_order(
    customers[6][0],   # Vignesh Raja
    distributors[2],   # Raj Enterprises
    [
        (black_rice, '1kg',  1),
        (moringa,    '100g', 2),
        (chilli,     '500g', 1),
    ]
)
print(f'  ✓ Order #{o3.pk} — {customers[6][0].get_full_name()} — ₹{o3.total_amount}')


# ─── Summary ────────────────────────────────────────────────────────────────

print('\n' + '='*60)
print('SEED COMPLETE — Login credentials (username = password = mobile)')
print('='*60)
print('\nSUPPLIERS')
for sd in supplier_data:
    print(f'  {sd["mobile"]} / {sd["mobile"]}  →  {sd["company"]}')
print('\nSTOCKIST')
print(f'  9876540001 / 9876540001  →  Rajan Stores')
print('\nDISTRIBUTORS')
for dd in distributor_data:
    print(f'  {dd["mobile"]} / {dd["mobile"]}  →  {dd["company"]}')
print('\nCUSTOMERS')
for mobile, name, dist_idx in customer_data:
    print(f'  {mobile} / {mobile}  →  {name}  ({distributor_data[dist_idx]["company"]})')
print('\nORDERS')
print(f'  Order #{o1.pk}  Lakshmi Devi  ₹{o1.total_amount}')
print(f'  Order #{o2.pk}  Sathish Babu  ₹{o2.total_amount}')
print(f'  Order #{o3.pk}  Vignesh Raja  ₹{o3.total_amount}')
print()
