"""
Seed a starter set of common product templates so the reuse library is useful
from day one. Idempotent — safe to run repeatedly (deduped by brand+name).

    venv\\Scripts\\python.exe manage.py seed_templates
"""
from decimal import Decimal
from django.core.management.base import BaseCommand


# (brand, name, category_slug, [(label, grams, suggested_price, suggested_mrp)])
STARTER = [
    ("Aavin", "Toned Milk", "other", [("500 ml", 500, 24, 26), ("1 L", 1000, 48, 52)]),
    ("", "Farm Eggs", "other", [("6 pcs", 0, 42, 48), ("12 pcs", 0, 84, 96), ("30 pcs tray", 0, 200, 230)]),
    ("Aashirvaad", "Atta Whole Wheat", "other", [("1 kg", 1000, 55, 62), ("5 kg", 5000, 265, 290)]),
    ("India Gate", "Basmati Rice", "other", [("1 kg", 1000, 120, 140), ("5 kg", 5000, 580, 650)]),
    ("Tata", "Toor Dal", "other", [("500 g", 500, 75, 85), ("1 kg", 1000, 145, 165)]),
    ("Tata", "Salt", "other", [("1 kg", 1000, 25, 28)]),
    ("Lux", "Soap Bar", "other", [("100 g", 100, 38, 45), ("3x100 g", 300, 108, 125)]),
    ("Dove", "Shampoo", "other", [("180 ml", 180, 135, 150), ("340 ml", 340, 240, 270)]),
    ("Colgate", "Toothpaste", "other", [("100 g", 100, 55, 62), ("200 g", 200, 102, 115)]),
    ("Parle-G", "Biscuits", "other", [("100 g", 100, 10, 10), ("250 g", 250, 25, 28)]),
    ("Bisleri", "Drinking Water", "other", [("1 L", 1000, 20, 22), ("2 L", 2000, 32, 35)]),
    ("Fortune", "Sunflower Oil", "other", [("1 L", 1000, 135, 150), ("5 L", 5000, 660, 720)]),
    ("Nescafe", "Instant Coffee", "other", [("50 g", 50, 160, 180), ("100 g", 100, 300, 330)]),
    ("Red Label", "Tea", "other", [("250 g", 250, 135, 150), ("500 g", 500, 260, 290)]),
    ("Surf Excel", "Detergent Powder", "other", [("1 kg", 1000, 110, 125), ("2 kg", 2000, 215, 240)]),
    ("Maggi", "Noodles", "other", [("70 g", 70, 14, 15), ("4-pack", 280, 55, 60)]),
    ("Amul", "Butter", "other", [("100 g", 100, 56, 62), ("500 g", 500, 265, 290)]),
    ("Saffola", "Gold Oil", "other", [("1 L", 1000, 170, 190), ("5 L", 5000, 820, 900)]),
]


class Command(BaseCommand):
    help = "Seed common product templates into the reuse library (idempotent)."

    def handle(self, *args, **opts):
        from products.models import Category, ProductTemplate, ProductTemplateVariant

        cat_by_slug = {c.slug: c for c in Category.objects.all()}
        created, updated = 0, 0
        for brand, name, slug, packs in STARTER:
            cat = cat_by_slug.get(slug)
            tmpl, was_created = ProductTemplate.objects.get_or_create(
                brand=brand, name=name,
                defaults={'category': cat, 'source': 'admin', 'is_active': True},
            )
            if was_created:
                created += 1
            else:
                updated += 1
            existing = {v.label.lower() for v in tmpl.variants.all()}
            for label, grams, price, mrp in packs:
                if label.lower() in existing:
                    continue
                ProductTemplateVariant.objects.create(
                    template=tmpl, label=label, unit_grams=grams,
                    suggested_price=Decimal(str(price)), suggested_mrp=Decimal(str(mrp)))

        total = ProductTemplate.objects.count()
        self.stdout.write(self.style.SUCCESS(
            f'Templates seeded: {created} new, {updated} existing. Library now has {total} templates.'))
