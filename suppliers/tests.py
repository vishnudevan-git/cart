"""Supplier surfaces — the stock-manage category filter and the add-product form."""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.factories import build_world, make_category, make_shared_product, login_as
from products.models import Product, ProductVariant
from distributors.models import DistributorVariantPrice


class StockManageCategoryFilterTests(TestCase):
    def setUp(self):
        self.world = build_world()
        # A second category with its own product, same supplier.
        self.spices = make_category(name='Spices', slug='spices', code='SP')
        self.pepper = make_shared_product(
            self.world.supplier, self.spices, name='Black Pepper', price='80', stock=40
        )
        self.client_s = login_as(self.client, self.world.supplier_user)
        self.url = reverse('suppliers:stock_manage')

    def test_unfiltered_shows_everything(self):
        body = self.client_s.get(self.url).content.decode()

        self.assertIn('Marketplace Cashew', body)
        self.assertIn('Black Pepper', body)

    def test_category_filter_scopes_the_rows(self):
        body = self.client_s.get(f'{self.url}?category={self.spices.pk}').content.decode()

        self.assertIn('Black Pepper', body)
        self.assertNotIn('Marketplace Cashew', body)

    def test_save_only_touches_filtered_rows_and_keeps_filter(self):
        cashew = self.world.shared_variant                 # Dry Fruits, stock 100
        pepper = self.pepper.variants.first()              # Spices, stock 40

        response = self.client_s.post(
            f'{self.url}?category={self.spices.pk}',
            {f'stock_{pepper.pk}': '35', f'stock_{cashew.pk}': '1'},
        )

        pepper.refresh_from_db()
        cashew.refresh_from_db()
        self.assertEqual(pepper.stock, 35)
        # The cashew field was smuggled into the POST but is outside the
        # filtered queryset — it must be untouched.
        self.assertEqual(cashew.stock, 100)
        self.assertIn(f'category={self.spices.pk}', response['Location'])


class SuggestedPriceTests(TestCase):
    """Goods with no printed MRP carry a *suggested* selling price so the shop
    keeps prices low. It rides the same variant formset as MRP, so guard that
    the value survives the JS → formset → DB round-trip."""

    def setUp(self):
        self.world = build_world()
        self.cat = make_category(name='Hardware', slug='hardware', code='HW')
        self.client_s = login_as(self.client, self.world.supplier_user)

    def _add(self, **over):
        data = {
            'name': 'No-Sticker Bolt',
            'category': str(self.cat.pk),
            'description': '',
            'stock_amount': '100', 'stock_unit': 'kg', 'price_per_kg': '200',
            'pricing_mode_field': 'auto',
            'show_mrp_field': 'false', 'show_suggested_field': 'true',
            'variants-TOTAL_FORMS': '1', 'variants-INITIAL_FORMS': '0',
            'variants-MIN_NUM_FORMS': '1', 'variants-MAX_NUM_FORMS': '10',
            'variants-0-label': '1kg', 'variants-0-selling_price': '200.00',
            'variants-0-mrp': '', 'variants-0-suggested_price': '240.00',
            'variants-0-stock': '100', 'variants-0-unit_grams': '1000',
            'variants-0-is_active': 'on', 'variants-0-id': '',
        }
        data.update(over)
        return self.client_s.post(reverse('suppliers:product_add'), data, follow=True)

    def test_suggested_price_saved_when_no_mrp(self):
        self._add()
        p = Product.all_objects.get(name='No-Sticker Bolt')
        self.assertTrue(p.show_suggested_price)
        self.assertFalse(p.show_mrp)
        self.assertEqual(p.variants.get().suggested_price, Decimal('240.00'))

    def test_suggested_ignored_flag_off(self):
        # Flag off → the product isn't marked as carrying a suggested price,
        # even if a stray value came through the formset.
        self._add(show_suggested_field='false')
        p = Product.all_objects.get(name='No-Sticker Bolt')
        self.assertFalse(p.show_suggested_price)

    def test_manual_prices_not_overwritten_on_add(self):
        # In MANUAL pricing mode the supplier's hand-typed per-pack price must
        # survive the save — auto-sync must not clobber it. (Regression: add
        # path used to run sync_variant_stocks() unconditionally.)
        self._add(
            name='Manual Priced Item',
            pricing_mode_field='manual',
            show_suggested_field='false',
            **{'variants-0-selling_price': '333.00', 'variants-0-mrp': ''},
        )
        p = Product.all_objects.get(name='Manual Priced Item')
        self.assertEqual(p.pricing_mode, 'manual')
        # 1kg auto price would be 200 (0.2/g × 1000); manual 333 must be kept.
        self.assertEqual(p.variants.get().selling_price, Decimal('333.00'))

    def test_mrp_and_suggested_can_both_be_on(self):
        # MRP (hard cap) and a suggested price (guidance) may both be enabled:
        # the shop cannot exceed the MRP and is cautioned toward a fair price.
        self._add(show_mrp_field='true', show_suggested_field='true',
                  **{'variants-0-mrp': '300.00', 'variants-0-suggested_price': '240.00'})
        p = Product.all_objects.get(name='No-Sticker Bolt')
        self.assertTrue(p.show_mrp)
        self.assertTrue(p.show_suggested_price)
        v = p.variants.get()
        self.assertEqual(v.mrp, Decimal('300.00'))
        self.assertEqual(v.suggested_price, Decimal('240.00'))


class VariantSaveIntegrityTests(TestCase):
    """Adding/editing a product must never leave a product with no packs, and an
    edit must NEVER hard-delete variant rows (that cascades to every shop price,
    stock row, price-history entry and order line pointing at the pack)."""

    def setUp(self):
        self.world = build_world()
        self.cat = make_category(name='Hardware', slug='hardware', code='HW')
        self.client_s = login_as(self.client, self.world.supplier_user)

    def _post(self, name, variants, **over):
        """variants: list of dicts (selling_price, unit_grams, label, ...)."""
        data = {
            'name': name, 'category': str(self.cat.pk), 'description': '',
            'stock_amount': '100', 'stock_unit': 'kg', 'price_per_kg': '200',
            'pricing_mode_field': 'manual',
            'show_mrp_field': 'false', 'show_suggested_field': 'false',
            'variants-TOTAL_FORMS': str(len(variants)), 'variants-INITIAL_FORMS': '0',
            'variants-MIN_NUM_FORMS': '1', 'variants-MAX_NUM_FORMS': '10',
        }
        for i, v in enumerate(variants):
            data[f'variants-{i}-label'] = v.get('label', '1kg')
            data[f'variants-{i}-selling_price'] = str(v.get('selling_price', '200'))
            data[f'variants-{i}-mrp'] = str(v.get('mrp', '') or '')
            data[f'variants-{i}-suggested_price'] = str(v.get('suggested_price', '') or '')
            data[f'variants-{i}-stock'] = str(v.get('stock', 100))
            data[f'variants-{i}-unit_grams'] = str(v.get('unit_grams', 1000))
            data[f'variants-{i}-is_active'] = 'on'
            data[f'variants-{i}-id'] = ''
        data.update(over)
        return data

    def _product_with_variant(self, label='1kg', ug=1000, price='200'):
        p = Product.objects.create(
            name='Editable', supplier=self.world.supplier, category=self.cat,
            status='approved', master_unit='g', master_stock_grams=0,
            master_price_per_unit=Decimal('0'), pricing_mode='manual', charges_confirmed=True,
        )
        v = ProductVariant.objects.create(product=p, label=label, selling_price=Decimal(price),
                                          stock=100, unit_grams=ug)
        return p, v

    # ── add ──
    def test_add_rejects_empty_variant_set(self):
        data = self._post('Zombie A', [])
        data['variants-TOTAL_FORMS'] = '0'
        self.client_s.post(reverse('suppliers:product_add'), data)
        self.assertFalse(Product.all_objects.filter(name='Zombie A').exists())

    def test_add_rejects_invalid_price_no_zombie(self):
        data = self._post('Zombie B', [{'selling_price': '0'}])   # price 0 → invalid
        self.client_s.post(reverse('suppliers:product_add'), data)
        self.assertFalse(Product.all_objects.filter(name='Zombie B').exists())

    # ── edit: no data loss ──
    def test_edit_invalid_preserves_variants_and_dvp(self):
        p, v = self._product_with_variant()
        dvp = DistributorVariantPrice.objects.create(
            distributor=self.world.shop_a, variant=v,
            base_price=Decimal('200'), selling_price=Decimal('250'))
        # Invalid edit (price 0) must save NOTHING.
        self.client_s.post(reverse('suppliers:product_edit', args=[p.pk]),
                           self._post('Editable', [{'selling_price': '0', 'unit_grams': 1000}]))
        v.refresh_from_db()
        self.assertEqual(v.selling_price, Decimal('200'))          # unchanged
        self.assertTrue(DistributorVariantPrice.objects.filter(pk=dvp.pk).exists())

    def test_edit_valid_updates_in_place_preserving_dvp(self):
        p, v = self._product_with_variant()
        dvp = DistributorVariantPrice.objects.create(
            distributor=self.world.shop_a, variant=v,
            base_price=Decimal('200'), selling_price=Decimal('250'))
        self.client_s.post(reverse('suppliers:product_edit', args=[p.pk]),
                           self._post('Editable', [{'selling_price': '210', 'unit_grams': 1000}]))
        v.refresh_from_db()
        self.assertEqual(v.selling_price, Decimal('210'))          # updated in place
        # The pack row (pk) survived, so the shop's price row still points at it.
        self.assertTrue(DistributorVariantPrice.objects.filter(pk=dvp.pk, variant=v).exists())

    def test_edit_removed_pack_is_deactivated_not_deleted(self):
        p, v1 = self._product_with_variant()
        v2 = ProductVariant.objects.create(product=p, label='500g', selling_price=Decimal('120'),
                                           stock=50, unit_grams=500)
        # Submit only the 1kg pack — the 500g pack should be SOFT-removed.
        self.client_s.post(reverse('suppliers:product_edit', args=[p.pk]),
                           self._post('Editable', [{'selling_price': '200', 'unit_grams': 1000}]))
        v2.refresh_from_db()
        self.assertTrue(ProductVariant.objects.filter(pk=v2.pk).exists())   # row kept
        self.assertFalse(v2.is_active)                                      # just deactivated
