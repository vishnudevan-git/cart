"""Distributors page: the customer count excludes the shop's own login."""
from django.test import TestCase
from django.urls import reverse

from accounts.factories import build_world, login_as


class DistributorCustomerCountTests(TestCase):
    def setUp(self):
        self.world = build_world()
        self.shop = self.world.shop_a
        self.client_admin = login_as(self.client, self.world.admin)

    def _count_for_shop(self):
        page = self.client_admin.get(reverse('admin_panel:distributor_list'))
        self.assertEqual(page.status_code, 200)
        for d in page.context['distributors']:
            if d.pk == self.shop.pk:
                return d.customer_count
        self.fail('shop not on the Distributors page')

    def test_count_is_customers_only(self):
        # Link the shop's own login to the shop, as in the live data.
        self.world.shop_a_user.distributor = self.shop
        self.world.shop_a_user.save(update_fields=['distributor'])
        self.assertEqual(self._count_for_shop(), 1)   # customer_a only

    def test_deleted_customers_are_not_counted(self):
        self.world.customer_a.is_deleted = True
        self.world.customer_a.save(update_fields=['is_deleted'])
        self.assertEqual(self._count_for_shop(), 0)
