"""Storefront must show products of approved suppliers.

Regression: _product_qs once filtered supplier__status='active', a value
Supplier.status never has ('approved' / 'suspended'), so every storefront
was empty.
"""
from django.test import TestCase

from accounts.factories import build_world
from distributors.models import DistributorProduct, DistributorVariantPrice
from products.views import _product_qs


class StorefrontSupplierStatusTests(TestCase):
    def setUp(self):
        self.world = build_world()
        self.shop = self.world.shop_a
        self.product = self.world.shared_product
        DistributorProduct.objects.create(distributor=self.shop, product=self.product, is_listed=True)

    def test_approved_supplier_product_is_shown(self):
        self.assertEqual(self.world.supplier.status, 'approved')
        self.assertIn(self.product, list(_product_qs(self.shop)))

    def test_suspended_supplier_product_hidden_without_downstream_price(self):
        self.world.supplier.status = 'suspended'
        self.world.supplier.save(update_fields=['status'])
        self.assertNotIn(self.product, list(_product_qs(self.shop)))

    def test_suspended_supplier_product_shown_with_downstream_price(self):
        DistributorVariantPrice.objects.create(
            distributor=self.shop, variant=self.world.shared_variant,
            base_price=500, selling_price=550, is_active=True,
        )
        self.world.supplier.status = 'suspended'
        self.world.supplier.save(update_fields=['status'])
        self.assertIn(self.product, list(_product_qs(self.shop)))
