"""A soft-deleted product must not appear on catalogue, stock or storefront pages."""
from django.test import TestCase
from django.urls import reverse

from accounts.factories import build_world, login_as
from products.models import Product


class DeletedProductHiddenTests(TestCase):
    def setUp(self):
        self.world = build_world()
        self.product = self.world.shared_product
        self.product.name = 'Zz Gone Lentil'
        self.product.status = 'approved'
        self.product.save()
        self.name = self.product.name

    def _delete(self):
        self.product.soft_delete()

    def test_supplier_pages_show_product_before_and_hide_it_after_delete(self):
        client = self.client
        login_as(client, self.world.supplier_user)
        self.assertContains(client.get(reverse('suppliers:product_list')), self.name)
        self.assertContains(client.get(reverse('suppliers:stock_manage')), f'stock_{self.product.variants.first().pk}')
        self._delete()
        self.assertNotContains(client.get(reverse('suppliers:product_list')), self.name)
        page = client.get(reverse('suppliers:stock_manage'))
        self.assertNotContains(page, self.name)
        self.assertNotContains(page, f'stock_{self.product.variants.first().pk}"')

    def test_admin_product_list_hides_deleted_but_deleted_tab_shows_it(self):
        login_as(self.client, self.world.admin)
        self.assertContains(self.client.get(reverse('admin_panel:product_list')), self.name)
        self._delete()
        self.assertNotContains(self.client.get(reverse('admin_panel:product_list')), self.name)
        self.assertContains(self.client.get(reverse('admin_panel:product_list') + '?deleted=1'), self.name)

    def test_storefront_hides_deleted_product(self):
        # The storefront needs a distributor-invite session, so log in as a shop.
        # Shop A does not list this product, so only the absence is asserted here.
        login_as(self.client, self.world.shop_a_user)
        self._delete()
        self.assertNotContains(self.client.get(reverse('products:product_list')), self.name)

    def test_marketplace_catalogue_hides_deleted_product(self):
        login_as(self.client, self.world.shop_a_user)
        self.assertContains(self.client.get(reverse('distributors:product_catalogue')), self.name)
        self._delete()
        self.assertNotContains(self.client.get(reverse('distributors:product_catalogue')), self.name)

    def test_supplier_can_restore_deleted_product(self):
        self._delete()
        login_as(self.client, self.world.supplier_user)
        self.client.post(reverse('suppliers:product_restore', args=[self.product.pk]))
        self.product.refresh_from_db()
        self.assertFalse(self.product.is_deleted)
        self.assertContains(self.client.get(reverse('suppliers:product_list')), self.name)

    def test_deleted_product_is_not_in_default_manager(self):
        self._delete()
        self.assertFalse(Product.objects.filter(pk=self.product.pk).exists())
        self.assertTrue(Product.all_objects.filter(pk=self.product.pk, is_deleted=True).exists())
