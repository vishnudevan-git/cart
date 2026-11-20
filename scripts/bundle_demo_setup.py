"""Bulk demo products for testing the bundle chain (kb/10-bundle-supply-chain.md).

Creates three bulk products from one supplier, each showing one bundle case,
lists them at one shop with prices, and seeds starting stock. Safe to re-run:
it reuses existing demo products by name and resets their demo stock and rules.

    A  Demo Bulk Rice 1 kg     bundle 10  supplier bundle-only, stockist singles
    B  Demo Bulk Sugar 1 kg    bundle 25  stockist bundle-only
    C  Demo Bulk Oil 1 L       bundle 12  distributor bundle-only

Starting stock: supplier 500, stockist (see STOCKIST_STOCK), distributor 0,
shop 5 (so customers can buy singles straight away).

Run (venv active, repo root):
    python manage.py shell -c "exec(open('scripts/bundle_demo_setup.py', encoding='utf-8').read())"

On another machine, change SHOP_ID / SUPPLIER_ID / CATEGORY_ID below to rows
that exist there. The shop must have a Distributor with an assigned stockist.
"""
from decimal import Decimal

from django.db import transaction

from distributors.models import (
    Distributor, DistributorProduct, DistributorStock, DistributorStockBatch,
    DistributorVariantPrice,
)
from distributors.wholesaler_models import WholesalerStock
from products.models import Category, Product, ProductBundle, ProductVariant, TierBundleRule
from stockist.models import StockistInventory
from suppliers.models import Supplier

SHOP_ID = 6          # Anna Nagar Organics
SUPPLIER_ID = 7      # Green Farms Organics
CATEGORY_ID = 8      # Staples

DEMO = [
    # name, pack label, unit size, unit, supplier price, bundle size, rule
    ('Demo Bulk Rice 1 kg', '1 kg', 1000, 'g', '60', 10, 'supplier_bundle_only'),
    ('Demo Bulk Sugar 1 kg', '1 kg', 1000, 'g', '45', 25, 'stockist_bundle_only'),
    ('Demo Bulk Oil 1 L', '1 L', 1000, 'ml', '150', 12, 'distributor_bundle_only'),
]
STOCKIST_STOCK = {'Demo Bulk Rice 1 kg': 30, 'Demo Bulk Sugar 1 kg': 50, 'Demo Bulk Oil 1 L': 24}
SHOP_STOCK = 5


def setup():
    shop = Distributor.objects.select_related('wholesaler__stockist').get(pk=SHOP_ID)
    wholesaler = shop.wholesaler
    if wholesaler is None or wholesaler.stockist is None:
        raise SystemExit('The shop needs a Distributor with an assigned stockist.')
    stockist = wholesaler.stockist
    supplier = Supplier.objects.get(pk=SUPPLIER_ID)
    category = Category.objects.get(pk=CATEGORY_ID)

    for name, label, size, unit, price, bundle_qty, rule in DEMO:
        product = Product.all_objects.filter(name=name, supplier=supplier).first()
        if product is None:
            product = Product.objects.create(
                supplier=supplier, category=category, name=name,
                description='Bulk demo product for testing bundle rules.',
                status='approved', charges_confirmed=True, allow_bundle=True,
                master_unit=unit, pricing_mode='manual',
            )
        else:
            Product.all_objects.filter(pk=product.pk).update(
                status='approved', charges_confirmed=True, is_deleted=False, allow_bundle=True,
            )
        variant = product.variants.filter(label=label).first()
        if variant is None:
            variant = ProductVariant.objects.create(
                product=product, label=label, selling_price=Decimal(price),
                mrp=Decimal(price) * Decimal('1.3'), stock=500, unit_grams=size,
            )
        ProductVariant.objects.filter(pk=variant.pk).update(
            stock=500, is_active=True, supplier_can_supply=True,
            only_bundle=(rule == 'supplier_bundle_only'),
        )
        variant.refresh_from_db()

        bundle, _ = ProductBundle.objects.get_or_create(
            product=product, variant=variant,
            defaults={'name': f'Case of {bundle_qty}', 'pack_quantity': bundle_qty},
        )
        ProductBundle.objects.filter(pk=bundle.pk).update(
            name=f'Case of {bundle_qty}', pack_quantity=bundle_qty, is_active=True,
        )

        # Bundle rules: clear old demo rules for this product, then set one.
        TierBundleRule.objects.filter(product=product).delete()
        if rule == 'stockist_bundle_only':
            TierBundleRule.objects.create(
                tier='stockist', owner_id=stockist.pk, scope='product',
                product=product, mode='bundle_only',
            )
        elif rule == 'distributor_bundle_only':
            TierBundleRule.objects.create(
                tier='wholesaler', owner_id=wholesaler.pk, scope='product',
                product=product, mode='bundle_only',
            )

        # Shop listing and prices (base = supplier price + 10%, sell = +25%).
        DistributorProduct.objects.update_or_create(
            distributor=shop, product=product, defaults={'is_listed': True},
        )
        base = (Decimal(price) * Decimal('1.10')).quantize(Decimal('1'))
        sell = (Decimal(price) * Decimal('1.25')).quantize(Decimal('1'))
        # Customers never pay below the chain cost (stockist charges + distributor
        # margin), so the listed price must not sit below that floor either.
        from stockist.models import get_stockist_price
        floor, _ = get_stockist_price(product, variant, stockist, enterprise=shop)
        sell = max(sell, Decimal(floor).quantize(Decimal('1')))
        DistributorVariantPrice.objects.update_or_create(
            distributor=shop, variant=variant,
            defaults={'base_price': base, 'selling_price': sell, 'is_active': True},
        )

        # Starting stock (demo seed only; real increases come from deliveries).
        StockistInventory.objects.update_or_create(
            stockist=stockist, variant=variant,
            defaults={'quantity': STOCKIST_STOCK[name]},
        )
        WholesalerStock.objects.filter(wholesaler=wholesaler, variant=variant).delete()
        shop_stock, _ = DistributorStock.objects.get_or_create(
            distributor=shop, variant=variant, defaults={'quantity': 0},
        )
        shop_stock.batches.all().delete()
        DistributorStockBatch.objects.create(
            stock=shop_stock, quantity=SHOP_STOCK, cost_price=base,
            note='Bundle demo starting stock',
        )
        shop_stock.refresh_quantity()

        print(f'{product.sku:16} {name:22} bundle {bundle_qty:>2}  rule={rule:24} '
              f'stockist={STOCKIST_STOCK[name]} shop={SHOP_STOCK} sell=Rs.{sell}')

    print(f'\nShop: {shop.company_name} | Distributor: {wholesaler.company_name} '
          f'(no buffer: {wholesaler.no_buffer_stock}) | Stockist: {stockist.company_name}')


with transaction.atomic():
    setup()
