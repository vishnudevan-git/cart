"""Checkout from the shop's own stock must not touch the supplier's stock.

Regression: checkout deducted the supplier variant stock for every cart line,
including lines filled from the shop's stock. The supplier's stock had already
left the supplier when it was delivered down the chain, so each shop sale
removed units twice.
"""
from django.test import TestCase
from django.urls import reverse

from accounts.factories import build_world, login_as
from cart.models import Cart
from distributors.models import DistributorStock, DistributorStockBatch, DistributorVariantPrice
from orders.models import Order


class ShopStockCheckoutTests(TestCase):
    def setUp(self):
        self.world = build_world()
        self.shop = self.world.shop_a
        self.customer = self.world.customer_a
        self.variant = self.world.shared_variant
        self.variant.stock = 100
        self.variant.save(update_fields=['stock'])
        DistributorVariantPrice.objects.create(
            distributor=self.shop, variant=self.variant,
            base_price=500, selling_price=600, is_active=True,
        )
        self.shop_stock = DistributorStock.objects.create(
            distributor=self.shop, variant=self.variant, quantity=0,
        )
        DistributorStockBatch.objects.create(stock=self.shop_stock, quantity=5, cost_price=500)
        self.shop_stock.refresh_quantity()
        self.client_c = login_as(self.client, self.customer)

    def checkout(self, qty):
        Cart.objects.create(customer=self.customer, variant=self.variant, quantity=qty)
        return self.client_c.post(reverse('orders:checkout'), {
            'address': 'Test Home, Chennai', 'phone': '9000000000',
        })

    def test_shop_sale_leaves_supplier_stock_alone(self):
        self.checkout(2)
        order = Order.objects.get(customer=self.customer)
        self.assertEqual(order.fulfillment_source, 'distributor_stock')
        self.variant.refresh_from_db()
        self.shop_stock.refresh_from_db()
        self.assertEqual(self.shop_stock.quantity, 3)
        self.assertEqual(self.variant.stock, 100, 'supplier stock was deducted for a shop sale')

    def test_supplier_sale_still_deducts_supplier_stock(self):
        self.checkout(8)           # more than the shop holds: goes to the supplier
        order = Order.objects.get(customer=self.customer)
        self.assertEqual(order.fulfillment_source, 'supplier')
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 92)
