"""Stock page layout: categories collapse, products collapse, Expand all, no Apply button."""
from django.test import TestCase
from django.urls import reverse

from accounts.factories import build_world, login_as, make_category, make_shared_product


class StockPageLayoutTests(TestCase):
    def setUp(self):
        self.world = build_world()
        self.client_s = login_as(self.client, self.world.supplier_user)
        self.spices = make_category(name='Spices', slug='spices', code='SP')
        make_shared_product(self.world.supplier, self.spices, name='Black Pepper', stock=40)
        self.body = self.client_s.get(reverse('suppliers:stock_manage')).content.decode()

    def test_categories_are_collapsible_sections(self):
        self.assertIn('class="stock-toggle stock-cat-head', self.body)
        self.assertIn('data-bs-target="#cat-1"', self.body)
        self.assertIn('Spices', self.body)

    def test_products_are_collapsed_until_opened(self):
        product = self.world.shared_product
        self.assertIn(f'data-bs-target="#prod-{product.pk}"', self.body)
        self.assertIn(f'class="collapse" id="prod-{product.pk}"', self.body)

    def test_expand_all_button_is_present(self):
        self.assertIn('id="expandAll"', self.body)
        self.assertIn('id="collapseAll"', self.body)

    def test_apply_button_is_removed_because_filter_submits_on_change(self):
        self.assertNotIn('>Apply<', self.body)
        self.assertIn('onchange="this.form.submit()"', self.body)

    def test_each_product_has_its_own_save_button(self):
        product = self.world.shared_product
        self.assertIn(f'id="card-{product.pk}"', self.body)
        self.assertIn(f'class="btn btn-success btn-sm save-product" data-product="{product.pk}"',
                      self.body)


class StockPageFilterTests(TestCase):
    """My Products filters on the stock page, applied on change (GET)."""

    def setUp(self):
        self.world = build_world()
        self.client_s = login_as(self.client, self.world.supplier_user)
        self.url = reverse('suppliers:stock_manage')
        spices = make_category(name='Spices', slug='spices', code='SP')
        self.other = make_shared_product(self.world.supplier, spices, name='Black Pepper', stock=40)
        self.cashew = self.world.shared_product

    def _body(self, query=''):
        return self.client_s.get(f'{self.url}{query}').content.decode()

    def test_filter_bar_has_my_products_filters_and_no_apply(self):
        body = self._body()
        for name in ('status', 'bundle', 'loose', 'mrp', 'unit', 'pack'):
            self.assertIn(f'<select name="{name}"', body)
        self.assertNotIn('>Apply<', body)

    def test_status_filter_hides_other_statuses(self):
        self.other.status = 'rejected'
        self.other.save(update_fields=['status'])
        body = self._body('?status=approved')
        self.assertIn('Marketplace Cashew', body)
        self.assertNotIn('Black Pepper', body)

    def test_bundle_filter_enabled_and_disabled(self):
        self.cashew.allow_bundle = True
        self.cashew.save(update_fields=['allow_bundle'])
        self.assertIn('Marketplace Cashew', self._body('?bundle=true'))
        self.assertNotIn('Marketplace Cashew', self._body('?bundle=false'))
        self.assertNotIn('Black Pepper', self._body('?bundle=true'))

    def test_save_keeps_the_filters_in_the_redirect(self):
        response = self.client_s.post(f'{self.url}?status=approved', {})
        self.assertEqual(response.status_code, 302)
        self.assertIn('status=approved', response['Location'])


class SaveOneProductTests(TestCase):
    def setUp(self):
        self.world = build_world()
        self.client_s = login_as(self.client, self.world.supplier_user)
        self.url = reverse('suppliers:stock_manage')
        spices = make_category(name='Spices', slug='spices', code='SP')
        self.other = make_shared_product(self.world.supplier, spices, name='Black Pepper', stock=40)
        self.other_variant = self.other.variants.first()
        self.variant = self.world.shared_variant          # stock 100

    def test_ajax_save_applies_only_the_posted_product(self):
        response = self.client_s.post(self.url, {
            f'stock_{self.variant.pk}': '60',
            f'orig_{self.variant.pk}': '100',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.variant.refresh_from_db()
        self.other_variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 60)
        self.assertEqual(self.other_variant.stock, 40)
        data = response.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['updated'], 1)

    def test_ajax_save_reports_conflicts_as_json(self):
        from products.models import ProductVariant
        ProductVariant.objects.filter(pk=self.variant.pk).update(stock=80)   # order arrives
        response = self.client_s.post(self.url, {
            f'stock_{self.variant.pk}': '60',
            f'orig_{self.variant.pk}': '100',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 80)
        data = response.json()
        self.assertFalse(data['ok'])
        self.assertIn('changed since you opened this page', data['skipped'][0]['reason'])

    def test_full_save_without_ajax_still_redirects(self):
        response = self.client_s.post(self.url, {
            f'stock_{self.variant.pk}': '60',
            f'orig_{self.variant.pk}': '100',
        })
        self.assertEqual(response.status_code, 302)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 60)
