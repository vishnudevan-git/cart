"""
seed_variant_demo — dummy multi-variant products for testing.

Creates a few products that each have 2+ variants (e.g. Premium / Regular),
with each variant carrying its own pack sizes, prices and stock. Safe to run
repeatedly (idempotent by SKU-ish name + supplier). Use --wipe to remove them.

Examples:
    python manage.py seed_variant_demo
    python manage.py seed_variant_demo --supplier 9876543201
    python manage.py seed_variant_demo --wipe
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction


# Each product: name, category slug, unit, and a list of variants.
# Each variant: name + packs [(label, unit_grams, selling_price, mrp, stock)].
DEMO = [
    {
        'name': 'Demo Basmati Rice',
        'category': ('Staples', 'staples', 'ST'),
        'unit': 'g',
        'variants': [
            ('Premium', [
                ('1kg', 1000, '150', '195', 40),
                ('5kg', 5000, '720', '900', 12),
            ]),
            ('Regular', [
                ('1kg', 1000, '110', '140', 60),
                ('5kg', 5000, '520', '650', 20),
            ]),
        ],
    },
    {
        'name': 'Demo Cooking Oil',
        'category': ('Staples', 'staples', 'ST'),
        'unit': 'ml',
        'variants': [
            ('Filtered', [
                ('1L', 1000, '180', '210', 50),
                ('5L', 5000, '850', '1000', 15),
            ]),
            ('Refined', [
                ('1L', 1000, '160', '190', 55),
                ('5L', 5000, '760', '900', 18),
            ]),
        ],
    },
    {
        'name': 'Demo Tea Powder',
        'category': ('Beverages', 'beverages', 'BV'),
        'unit': 'g',
        'variants': [
            ('Gold', [
                ('250g', 250, '140', '175', 30),
                ('500g', 500, '270', '340', 20),
            ]),
            ('Strong', [
                ('250g', 250, '120', '150', 35),
                ('500g', 500, '230', '290', 22),
            ]),
            ('Green', [
                ('250g', 250, '160', '200', 15),
            ]),
        ],
    },
]


class Command(BaseCommand):
    help = 'Seed dummy multi-variant products for testing the variant feature.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--supplier', default=None,
            help='Supplier username/mobile to own the demo products. '
                 'Defaults to the first supplier found.'
        )
        parser.add_argument(
            '--wipe', action='store_true',
            help='Remove the demo products instead of creating them.'
        )

    def handle(self, *args, **opts):
        from products.models import Category, Product, ProductVariant, ProductVariantType
        from suppliers.models import Supplier

        names = [d['name'] for d in DEMO]

        if opts['wipe']:
            qs = Product.all_objects.filter(name__in=names)
            n = qs.count()
            for p in qs:
                ProductVariant.objects.filter(product=p).delete()
                ProductVariantType.objects.filter(product=p).delete()
            qs.delete()
            self.stdout.write(self.style.SUCCESS(f'Wiped {n} demo product(s).'))
            return

        # Resolve the owning supplier.
        supplier = None
        if opts['supplier']:
            supplier = Supplier.objects.filter(
                user__username=opts['supplier']
            ).first() or Supplier.objects.filter(code=opts['supplier']).first()
            if supplier is None:
                self.stderr.write(self.style.ERROR(
                    f'No supplier matched "{opts["supplier"]}".'))
                return
        else:
            supplier = Supplier.objects.order_by('id').first()
            if supplier is None:
                self.stderr.write(self.style.ERROR(
                    'No suppliers exist. Create one first (or run the full seed).'))
                return

        self.stdout.write(f'Using supplier: {supplier.company_name} (id={supplier.id})')

        created_products = 0
        with transaction.atomic():
            for spec in DEMO:
                cat_name, cat_slug, cat_code = spec['category']
                category, _ = Category.objects.get_or_create(
                    slug=cat_slug, defaults={'name': cat_name, 'code': cat_code}
                )

                product = Product.all_objects.filter(
                    name=spec['name'], supplier=supplier
                ).first()
                if product is None:
                    product = Product.objects.create(
                        name=spec['name'], supplier=supplier, category=category,
                        description=f'{spec["name"]} — demo multi-variant product.',
                        status='approved', charges_confirmed=True,
                        master_unit=spec['unit'], pricing_mode='manual',
                        show_mrp=True,
                    )
                    created_products += 1
                else:
                    # Reset variants on re-seed so counts stay predictable.
                    ProductVariant.objects.filter(product=product).delete()
                    ProductVariantType.objects.filter(product=product).delete()

                for pos, (vname, packs) in enumerate(spec['variants']):
                    vt = ProductVariantType.objects.create(
                        product=product, name=vname, position=pos, is_active=True
                    )
                    for label, ug, price, mrp, stock in packs:
                        ProductVariant.objects.create(
                            product=product, variant_type=vt,
                            label=label, unit_grams=ug,
                            selling_price=Decimal(price), mrp=Decimal(mrp),
                            stock=stock, is_active=True,
                        )

                self.stdout.write(self.style.SUCCESS(
                    f'  ✓ {product.name} [{product.sku}] — '
                    f'{len(spec["variants"])} variants'
                ))

        self.stdout.write(self.style.SUCCESS(
            f'\nDone. {created_products} new product(s); '
            f'{len(DEMO)} demo products ready.'
        ))
        self.stdout.write('Edit them at /supplier/products/  → each shows a variant switcher.')
