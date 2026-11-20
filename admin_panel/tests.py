from django.test import TestCase
from django.urls import reverse

from accounts.factories import build_world, login_as


class AdminStockSearchTests(TestCase):
    def setUp(self):
        self.world = build_world()
        self.client = login_as(self.client, self.world.admin)

    def test_stock_search_matches_product_sku_supplier_and_category(self):
        for term in (
            self.world.shared_product.name,
            self.world.shared_product.sku,
            self.world.supplier.company_name,
            self.world.category.name,
        ):
            with self.subTest(term=term):
                response = self.client.get(reverse('admin_panel:stock_adjust'), {'q': term})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    [product.pk for product in response.context['products']],
                    [self.world.shared_product.pk],
                )

    def test_stock_search_hides_non_matching_products(self):
        response = self.client.get(
            reverse('admin_panel:stock_adjust'),
            {'q': 'does-not-match-any-product'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['products'], [])

    def test_stock_status_filter_shows_active_and_suspended_suppliers_separately(self):
        self.world.supplier.status = 'suspended'
        self.world.supplier.save(update_fields=['status'])

        active_response = self.client.get(reverse('admin_panel:stock_adjust'))
        suspended_response = self.client.get(
            reverse('admin_panel:stock_adjust'),
            {'show_suspended': '1'},
        )

        self.assertEqual(active_response.context['products'], [])
        self.assertEqual(
            [product.pk for product in suspended_response.context['products']],
            [self.world.shared_product.pk],
        )
        self.assertEqual(list(suspended_response.context['suppliers']), [self.world.supplier])


class AdminProductPurgeTests(TestCase):
    def setUp(self):
        self.world = build_world()
        self.client = login_as(self.client, self.world.admin)

    def test_purge_permanently_deletes_only_soft_deleted_products_and_variants(self):
        product = self.world.shared_product
        product.soft_delete()

        response = self.client.post(
            reverse('admin_panel:product_purge', args=[product.pk]),
        )

        self.assertRedirects(response, f"{reverse('admin_panel:product_list')}?deleted=1")
        self.assertFalse(type(product).objects.filter(pk=product.pk).exists())
        self.assertFalse(type(self.world.shared_variant).objects.filter(pk=self.world.shared_variant.pk).exists())

    def test_purge_does_not_delete_active_products(self):
        response = self.client.post(
            reverse('admin_panel:product_purge', args=[self.world.shared_product.pk]),
        )

        self.assertEqual(response.status_code, 404)
        self.assertTrue(type(self.world.shared_product).objects.filter(pk=self.world.shared_product.pk).exists())

    def test_purge_requires_post(self):
        response = self.client.get(
            reverse('admin_panel:product_purge', args=[self.world.shared_product.pk]),
        )

        self.assertEqual(response.status_code, 405)
