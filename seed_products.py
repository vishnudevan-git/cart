"""
Seeds sample suppliers, products with variants, and a global commission rule.
Run: python seed_products.py
"""
import os, django, urllib.request
from pathlib import Path
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'lakrish_mart.settings')
django.setup()

from django.core.files import File
from accounts.models import User
from suppliers.models import Supplier
from products.models import Product, Category, ProductVariant
from payments.models import CommissionRule

MEDIA_PRODUCTS = Path('media/products')
MEDIA_PRODUCTS.mkdir(parents=True, exist_ok=True)

# (name, category_slug, description, image_url, filename, variants[(label, mrp, price, stock)])
PRODUCTS = [
    ("Organic Turmeric Powder", "organic-products",
     "Pure organic turmeric powder, rich in curcumin.",
     "https://images.unsplash.com/photo-1615485500704-8e990f9900f7?w=400&q=80", "turmeric.jpg",
     [("100g", 100, 80, 150), ("250g", 220, 180, 100), ("500g", 400, 320, 60)]),

    ("Organic Honey", "organic-products",
     "Raw unprocessed forest honey. 100% natural.",
     "https://images.unsplash.com/photo-1587049352846-4a222e784d38?w=400&q=80", "honey.jpg",
     [("250g", 200, 175, 80), ("500g", 380, 350, 60), ("1kg", 720, 650, 40)]),

    ("Cold-Pressed Coconut Oil", "organic-products",
     "Virgin cold-pressed coconut oil.",
     "https://images.unsplash.com/photo-1526947425960-945c6e72858f?w=400&q=80", "coconut_oil.jpg",
     [("250ml", 180, 149, 100), ("500ml", 340, 299, 70), ("1L", 620, 549, 40)]),

    ("Premium Medjool Dates", "dry-fruits",
     "Soft, sweet Medjool dates. High in fibre.",
     "https://images.unsplash.com/photo-1609501676725-7186f017a4b7?w=400&q=80", "dates.jpg",
     [("250g", 280, 249, 80), ("500g", 540, 499, 60), ("1kg", 1000, 899, 30)]),

    ("Dried Apricots", "dry-fruits",
     "Sun-dried Turkish apricots. No preservatives.",
     "https://images.unsplash.com/photo-1597714026720-8f74c62310ba?w=400&q=80", "apricots.jpg",
     [("100g", 100, 85, 120), ("250g", 240, 220, 80), ("500g", 450, 399, 50)]),

    ("Black Raisins", "dry-fruits",
     "Seedless black raisins, naturally dried.",
     "https://images.unsplash.com/photo-1615485290382-441e4d049cb5?w=400&q=80", "raisins.jpg",
     [("250g", 100, 85, 150), ("500g", 190, 160, 100)]),

    ("California Almonds", "nuts",
     "Premium California almonds. Rich in Vitamin E.",
     "https://images.unsplash.com/photo-1508061253366-f7da158b6d46?w=400&q=80", "almonds.jpg",
     [("250g", 380, 329, 80), ("500g", 720, 649, 60), ("1kg", 1380, 1249, 30)]),

    ("Cashews W240", "nuts",
     "Whole cashews, grade W240. Creamy and crunchy.",
     "https://images.unsplash.com/photo-1567892737950-30c4db37cd89?w=400&q=80", "cashews.jpg",
     [("250g", 340, 299, 70), ("500g", 660, 599, 50), ("1kg", 1280, 1149, 25)]),

    ("Walnuts", "nuts",
     "Fresh walnuts. Excellent source of Omega-3.",
     "https://images.unsplash.com/photo-1606923829579-0cb981a83e2e?w=400&q=80", "walnuts.jpg",
     [("250g", 399, 349, 80), ("500g", 780, 699, 50)]),

    ("Pistachios Roasted & Salted", "nuts",
     "Roasted and lightly salted pistachios.",
     "https://images.unsplash.com/photo-1590080875515-8a3a8dc5735e?w=400&q=80", "pistachios.jpg",
     [("100g", 180, 149, 100), ("250g", 440, 399, 60), ("500g", 860, 779, 35)]),

    ("Kashmiri Red Chilli Powder", "spices",
     "Vibrant red Kashmiri chilli. Mild heat, deep colour.",
     "https://images.unsplash.com/photo-1596040033229-a9821ebd058d?w=400&q=80", "chilli.jpg",
     [("100g", 70, 60, 200), ("200g", 130, 120, 150), ("500g", 300, 270, 80)]),

    ("Whole Black Pepper", "spices",
     "Bold whole black peppercorns from Kerala.",
     "https://images.unsplash.com/photo-1506368249639-73a05d6f6488?w=400&q=80", "pepper.jpg",
     [("50g", 60, 50, 200), ("100g", 110, 95, 150), ("250g", 260, 220, 80)]),

    ("Cinnamon Sticks", "spices",
     "True Ceylon cinnamon sticks.",
     "https://images.unsplash.com/photo-1608198093002-ad4e005484ec?w=400&q=80", "cinnamon.jpg",
     [("50g", 55, 45, 200), ("100g", 100, 89, 150)]),

    ("Chia Seeds", "other",
     "Organic chia seeds. Superfood packed with protein.",
     "https://images.unsplash.com/photo-1514733670139-4d87a1941d55?w=400&q=80", "chia.jpg",
     [("100g", 120, 99, 150), ("250g", 280, 249, 100), ("500g", 530, 469, 60)]),

    ("Flaxseeds", "other",
     "Raw golden flaxseeds. High in Omega-3.",
     "https://images.unsplash.com/photo-1610725664285-7c57e6eeac3f?w=400&q=80", "flaxseeds.jpg",
     [("250g", 70, 59, 200), ("500g", 130, 99, 150)]),
]

def get_or_create_supplier(username, company, category_key):
    if not User.objects.filter(username=username).exists():
        user = User.objects.create_user(username=username, password='supplier123', role='supplier', email=f'{username}@lakrish.com')
    else:
        user = User.objects.get(username=username)
    supplier, _ = Supplier.objects.get_or_create(user=user, defaults={
        'company_name': company, 'category': category_key, 'status': 'approved',
    })
    return supplier

def download_image(url, filename):
    dest = MEDIA_PRODUCTS / filename
    if dest.exists():
        return dest
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as r:
            dest.write_bytes(r.read())
        print(f"  Downloaded {filename}")
    except Exception as e:
        print(f"  Could not download {filename}: {e}")
        return None
    return dest

suppliers = {
    'organic-products': get_or_create_supplier('green_farms', 'Green Farms Organics', 'organic'),
    'dry-fruits':       get_or_create_supplier('nature_basket', 'Nature Basket', 'dry_fruits'),
    'nuts':             get_or_create_supplier('nutri_hub', 'NutriHub Traders', 'nuts'),
    'spices':           get_or_create_supplier('spice_valley', 'Spice Valley', 'spices'),
    'other':            get_or_create_supplier('health_store', 'Health Store India', 'other'),
}

# Add a global commission rule of 10%
if not CommissionRule.objects.filter(rule_type='overall').exists():
    CommissionRule.objects.create(rule_type='overall', rate=10)
    print("Global commission rule created: 10%")

created = 0
for name, cat_slug, desc, img_url, img_file, variant_data in PRODUCTS:
    if Product.objects.filter(name=name).exists():
        print(f"Skipping (exists): {name}")
        continue

    category = Category.objects.filter(slug=cat_slug).first()
    supplier = suppliers.get(cat_slug)

    product = Product(name=name, category=category, supplier=supplier,
                      description=desc, status='approved', rating=4.2)

    img_path = download_image(img_url, img_file)
    if img_path:
        with open(img_path, 'rb') as f:
            product.image.save(img_file, File(f), save=False)
    product.save()

    for label, mrp, price, stock in variant_data:
        ProductVariant.objects.create(product=product, label=label, mrp=mrp, selling_price=price, stock=stock)

    print(f"Created: {name} ({len(variant_data)} variants)")
    created += 1

print(f"\nDone. {created} products added.")
