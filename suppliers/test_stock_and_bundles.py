"""Regression tests: supplier bundle saves and conflict-safe stock saves."""
import json

from django.test import TestCase
from django.urls import reverse

from accounts.factories import build_world, login_as, make_category, make_shared_product
from products.models import Product, ProductBundle, ProductVariantType
from stockist.models import StockistInventory


def _product_payload(cat_pk, groups, allow_bundle='1', bundle_rows=None, **over):
    """Form data shaped like the Add/Edit product page.

    ``bundle_rows`` mirrors what product_form.html sends today: one hidden row
    per pack size with ``bundle_id[]`` always empty.
    """
    data = {
        'name': 'Bundle Rice',
        'category': str(cat_pk),
        'description': '',
        'stock_amount': '100', 'stock_unit': 'kg', 'price_per_kg': '200',
        'pricing_mode_field': 'manual',
        'show_mrp_field': 'false', 'show_suggested_field': 'false',
        'allow_bundle': allow_bundle,
        'variants_json': json.dumps(groups),
    }
    if bundle_rows:
        data['bundle_id[]'] = [''] * len(bundle_rows)
        data['bundle_name[]'] = [r[0] for r in bundle_rows]
        data['bundle_unit_grams[]'] = [str(r[1]) for r in bundle_rows]
        data['bundle_qty[]'] = [str(r[2]) for r in bundle_rows]
    data.update(over)
    return data


class BundleSaveTests(TestCase):
    def setUp(self):
        self.world = build_world()
        self.cat = make_category(name='Grains', slug='grains', code='GR')
        self.client_s = login_as(self.client, self.world.supplier_user)

    def _groups(self):
        # Two variant types that both sell a 1 kg pack.
        return [
            {'name': 'Basmati', 'packs': [{
                'label': '1kg', 'unit_grams': 1000, 'selling_price': '200.00',
                'stock': 48, 'bundle_qty': 12, 'bundle_count': 4, 'bundle_remainder': 0, 'supplier_can_supply': True,
            }]},
            {'name': 'Sona', 'packs': [{
                'label': '1kg', 'unit_grams': 1000, 'selling_price': '180.00',
                'stock': 48, 'bundle_qty': 12, 'bundle_count': 4, 'bundle_remainder': 0, 'supplier_can_supply': True,
            }]},
        ]

    def _rows(self):
        return [('Box of 12 (1kg)', 1000, 12)]

    def test_each_pack_keeps_its_own_bundle_after_add(self):
        self.client_s.post(
            reverse('suppliers:product_add'),
            _product_payload(self.cat.pk, self._groups(), bundle_rows=self._rows()),
            follow=True,
        )
        product = Product.all_objects.get(name='Bundle Rice')
        for vt in ProductVariantType.objects.filter(product=product):
            pack = vt.packs.get()
            bundles = ProductBundle.objects.filter(variant=pack, is_active=True)
            self.assertEqual(bundles.count(), 1, f'pack in {vt.name!r} has no bundle')
            self.assertEqual(bundles.get().pack_quantity, 12)

    def test_resaving_product_does_not_duplicate_bundles(self):
        self.client_s.post(
            reverse('suppliers:product_add'),
            _product_payload(self.cat.pk, self._groups(), bundle_rows=self._rows()),
            follow=True,
        )
        product = Product.all_objects.get(name='Bundle Rice')
        groups = self._groups()
        for vt, group in zip(ProductVariantType.objects.filter(product=product).order_by('position'), groups):
            group['id'] = vt.pk
        self.client_s.post(
            reverse('suppliers:product_edit', args=[product.pk]),
            _product_payload(self.cat.pk, groups, bundle_rows=self._rows()),
            follow=True,
        )
        self.assertEqual(ProductBundle.objects.filter(product=product).count(), 2)
        self.assertEqual(ProductBundle.objects.filter(product=product, is_active=True).count(), 2)

    def _add_and_get_groups(self):
        self.client_s.post(
            reverse('suppliers:product_add'),
            _product_payload(self.cat.pk, self._groups(), bundle_rows=self._rows()),
            follow=True,
        )
        product = Product.all_objects.get(name='Bundle Rice')
        groups = self._groups()
        for vt, group in zip(ProductVariantType.objects.filter(product=product).order_by('position'), groups):
            group['id'] = vt.pk
        return product, groups

    def test_changing_box_size_updates_existing_bundle(self):
        product, groups = self._add_and_get_groups()
        for group in groups:
            group['packs'][0]['bundle_qty'] = 6
            group['packs'][0]['bundle_count'] = 8
        self.client_s.post(
            reverse('suppliers:product_edit', args=[product.pk]),
            _product_payload(self.cat.pk, groups),
            follow=True,
        )
        active = ProductBundle.objects.filter(product=product, is_active=True)
        self.assertEqual(active.count(), 2)
        self.assertEqual(sorted(active.values_list('pack_quantity', flat=True)), [6, 6])

    def test_turning_bundles_off_deactivates_them(self):
        product, groups = self._add_and_get_groups()
        for group in groups:
            pack = group['packs'][0]
            pack.update(bundle_qty=0, bundle_count=None, stock=48, stock_entered=True)
        self.client_s.post(
            reverse('suppliers:product_edit', args=[product.pk]),
            _product_payload(self.cat.pk, groups, allow_bundle='0'),
            follow=True,
        )
        self.assertFalse(ProductBundle.objects.filter(product=product, is_active=True).exists())


class BundleFieldsRequiredTests(TestCase):
    """Allow Bundle on must come with packs per bundle and a bundle count."""

    setUp = BundleSaveTests.setUp
    _groups = BundleSaveTests._groups
    _rows = BundleSaveTests._rows

    def _blank_bundle_groups(self):
        groups = self._groups()
        for group in groups:
            pack = group['packs'][0]
            pack.update(bundle_qty=0, bundle_count=None, stock=48, stock_entered=True)
        return groups

    def test_add_with_bundle_on_and_no_packs_per_bundle_is_refused(self):
        response = self.client_s.post(
            reverse('suppliers:product_add'),
            _product_payload(self.cat.pk, self._blank_bundle_groups()),
            follow=True,
        )
        self.assertFalse(Product.all_objects.filter(name='Bundle Rice').exists())
        self.assertContains(response, 'enter packs per bundle for at least one pack size')

    def test_add_with_packs_per_bundle_but_blank_bundle_count_is_refused(self):
        groups = self._groups()
        for group in groups:
            group['packs'][0]['bundle_count'] = None
        response = self.client_s.post(
            reverse('suppliers:product_add'),
            _product_payload(self.cat.pk, groups),
            follow=True,
        )
        self.assertFalse(Product.all_objects.filter(name='Bundle Rice').exists())
        self.assertContains(response, 'enter a bundle count of 0 or more')

    def test_edit_clearing_packs_per_bundle_with_bundle_on_is_refused(self):
        self.client_s.post(
            reverse('suppliers:product_add'),
            _product_payload(self.cat.pk, self._groups(), bundle_rows=self._rows()),
            follow=True,
        )
        product = Product.all_objects.get(name='Bundle Rice')
        groups = self._blank_bundle_groups()
        for vt, group in zip(ProductVariantType.objects.filter(product=product).order_by('position'), groups):
            group['id'] = vt.pk
        response = self.client_s.post(
            reverse('suppliers:product_edit', args=[product.pk]),
            _product_payload(self.cat.pk, groups),
            follow=True,
        )
        self.assertEqual(ProductBundle.objects.filter(product=product, is_active=True).count(), 2)
        self.assertContains(response, 'enter packs per bundle for at least one pack size')


class StockSaveSafetyTests(TestCase):
    def setUp(self):
        self.world = build_world()
        self.client_s = login_as(self.client, self.world.supplier_user)
        self.url = reverse('suppliers:stock_manage')
        self.variant = self.world.shared_variant          # stock 100
        self.variant.stock = 100
        self.variant.save(update_fields=['stock'])

    def _post(self, **data):
        return self.client_s.post(self.url, data, follow=True)

    def test_unchanged_row_does_not_overwrite_newer_stock(self):
        # Page loaded at 100; an order drops the live count to 80 meanwhile.
        ProductVariant = type(self.variant)
        ProductVariant.objects.filter(pk=self.variant.pk).update(stock=80)
        self._post(**{f'stock_{self.variant.pk}': '100', f'orig_{self.variant.pk}': '100'})
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 80)

    def test_edit_conflicting_with_newer_stock_is_rejected(self):
        ProductVariant = type(self.variant)
        ProductVariant.objects.filter(pk=self.variant.pk).update(stock=80)
        response = self._post(**{f'stock_{self.variant.pk}': '50', f'orig_{self.variant.pk}': '100'})
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 80)
        self.assertContains(response, 'changed since you opened this page')

    def test_normal_edit_still_saves(self):
        self._post(**{f'stock_{self.variant.pk}': '60', f'orig_{self.variant.pk}': '100'})
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 60)

    def test_missing_original_falls_back_to_plain_save(self):
        self._post(**{f'stock_{self.variant.pk}': '35'})
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 35)

    def _loose_product(self):
        product = make_shared_product(self.world.supplier, self.world.category,
                                      name='Loose Dal', stock=10)
        product.allow_loose_distribution = True
        product.save()
        vt = ProductVariantType.objects.create(product=product, name='', master_stock_grams=5000)
        variant = product.variants.first()
        variant.variant_type = vt
        variant.unit_grams = 1000
        variant.save()
        return product, vt

    def test_non_finite_pool_value_is_rejected_not_a_server_error(self):
        _, vt = self._loose_product()
        for bad in ('NaN', 'Infinity', '-Infinity'):
            response = self._post(**{f'pool_vt_{vt.pk}': bad, f'orig_pool_{vt.pk}': '5000'})
            self.assertEqual(response.status_code, 200)
        vt.refresh_from_db()
        self.assertEqual(vt.master_stock_grams, 5000)

    def test_pool_conflict_is_rejected(self):
        _, vt = self._loose_product()
        ProductVariantType.objects.filter(pk=vt.pk).update(master_stock_grams=3000)
        self._post(**{f'pool_vt_{vt.pk}': '2', f'orig_pool_{vt.pk}': '5000'})
        vt.refresh_from_db()
        self.assertEqual(vt.master_stock_grams, 3000)


class DownstreamTotalUnitTests(TestCase):
    def setUp(self):
        self.world = build_world()
        self.cat = make_category(name='Pulses', slug='pulses', code='PU')
        self.product = make_shared_product(self.world.supplier, self.cat, name='Toor Dal', stock=0)
        self.small = self.product.variants.first()          # 500 g, from helper
        self.small.label, self.small.unit_grams = '500 g', 500
        self.small.save()
        from products.models import ProductVariant
        self.large = ProductVariant.objects.create(
            product=self.product, label='1 kg', selling_price='190', stock=0, unit_grams=1000)
        StockistInventory.objects.create(stockist=self.world.stockist, variant=self.small, quantity=2)
        StockistInventory.objects.create(stockist=self.world.stockist, variant=self.large, quantity=1)

    def test_total_is_in_grams_not_mixed_pack_counts(self):
        from products.stock_units import attach_downstream_base_totals
        attach_downstream_base_totals([self.product])
        self.assertEqual(self.product.ds_stockist_base, 2 * 500 + 1 * 1000)

    def test_label_is_human_readable(self):
        from products.stock_units import format_base_units
        self.assertEqual(format_base_units(2000, 'g'), '2 kg')
        self.assertEqual(format_base_units(750, 'g'), '750 g')
        self.assertEqual(format_base_units(1500, 'ml'), '1.5 L')

    def test_stock_page_shows_total_in_grams(self):
        client_s = login_as(self.client, self.world.supplier_user)
        body = client_s.get(reverse('suppliers:stock_manage')).content.decode()
        self.assertIn('= 2 kg', body)


class StockPageBundleCountTests(TestCase):
    """Bundle packs on the stock page take a bundle count (count x box + leftover)."""

    def setUp(self):
        self.world = build_world()
        self.client_s = login_as(self.client, self.world.supplier_user)
        self.product = self.world.shared_product
        self.product.allow_bundle = True
        self.product.save()
        self.variant = self.world.shared_variant          # stock 100
        ProductBundle.objects.create(
            product=self.product, variant=self.variant, name='Box of 12', pack_quantity=12)
        self.url = reverse('suppliers:stock_manage')

    def _post_count(self, count, orig=None):
        orig = self.variant.stock if orig is None else orig
        return self.client_s.post(self.url, {
            f'bundle_count_{self.variant.pk}': count,
            f'orig_{self.variant.pk}': str(orig),
        }, follow=True)

    def test_page_shows_bundle_count_input_with_current_count(self):
        body = self.client_s.get(self.url).content.decode()
        self.assertIn(f'name="bundle_count_{self.variant.pk}" value="8"', body)
        self.assertIn('whole-only', body)
        self.assertIn('4 packs left over', body)

    def test_bundle_count_saves_as_count_times_box_plus_leftover(self):
        self._post_count('5')                             # 5 x 12 + 4 leftover
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 64)

    def test_unchanged_bundle_count_changes_nothing(self):
        self._post_count('8')
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 100)

    def test_non_digit_bundle_count_is_refused(self):
        response = self._post_count('1e2')
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 100)
        self.assertContains(response, 'not a whole number')

    def test_empty_bundle_count_is_refused(self):
        self._post_count('')
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 100)

    def test_stale_page_does_not_overwrite_order(self):
        from products.models import ProductVariant
        ProductVariant.objects.filter(pk=self.variant.pk).update(stock=90)   # order arrived
        self._post_count('5', orig=100)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 90)


class BundleStockReadOnlyTests(TestCase):
    """The stock page shows bundle counts only. Box size is set on the product edit page."""

    def setUp(self):
        self.world = build_world()
        self.client_s = login_as(self.client, self.world.supplier_user)
        self.product = self.world.shared_product
        self.product.allow_bundle = True
        self.product.save()
        self.variant = self.world.shared_variant          # 500 g, stock 100
        self.bundle = ProductBundle.objects.create(
            product=self.product, variant=self.variant, name='Box of 12', pack_quantity=12)
        self.url = reverse('suppliers:stock_manage')

    def test_stock_page_shows_bundle_count_not_box_input(self):
        body = self.client_s.get(self.url).content.decode()
        self.assertIn('value="8"', body)            # the bundle count input for the pack
        self.assertNotIn(f'name="bundle_qty_{self.bundle.pk}"', body)

    def test_posted_box_size_is_ignored_on_stock_page(self):
        self.client_s.post(self.url, {
            f'stock_{self.variant.pk}': str(self.variant.stock),
            f'orig_{self.variant.pk}': str(self.variant.stock),
            f'bundle_qty_{self.bundle.pk}': '6',
            f'bundle_orig_{self.bundle.pk}': '12',
        }, follow=True)
        self.bundle.refresh_from_db()
        self.assertEqual(self.bundle.pack_quantity, 12)
        self.assertEqual(self.bundle.name, 'Box of 12')
