"""End-to-end stock flow, through the real checkout, cart and request views (kb/13, kb/14).

Each class is one scenario from the owner's rules. A scenario checks the stock counts at
every tier and the source recorded on the order item.
"""
from django.test import TestCase
from django.urls import reverse

from accounts.factories import build_world, login_as
from cart.models import Cart
from distributors.models import DistributorStock, DistributorStockBatch, DistributorVariantPrice, RestockRequest
from distributors.shop_blocking import block_for_shop_request
from distributors.wholesaler_models import WholesalerStock
from orders.models import Order, OrderItem
from products.models import ProductVariant
from stockist.models import StockistInventory


class StockFlowBase(TestCase):
    def setUp(self):
        self.world = build_world()
        self.shop = self.world.shop_a
        self.customer = self.world.customer_a
        self.variant = self.world.shared_variant
        ProductVariant.objects.filter(pk=self.variant.pk).update(stock=100)
        self.variant.refresh_from_db()
        self.world.wholesaler.stockist = self.world.stockist
        self.world.wholesaler.save(update_fields=['stockist'])
        DistributorVariantPrice.objects.create(
            distributor=self.shop, variant=self.variant,
            base_price=500, selling_price=600, is_active=True,
        )
        self.client_c = login_as(self.client_class(), self.customer)

    def supplier_stock(self):
        return ProductVariant.objects.get(pk=self.variant.pk).stock

    def checkout(self, qty):
        Cart.objects.create(customer=self.customer, variant=self.variant, quantity=qty)
        self.client_c.post(reverse('orders:checkout'), {
            'address': 'Test Home, Chennai', 'phone': '9000000000',
        })
        return Order.objects.filter(customer=self.customer).order_by('-pk').first()


class CheckoutNearestFirstTests(StockFlowBase):
    def test_stockist_stock_is_used_before_supplier(self):
        inv = StockistInventory.objects.create(
            stockist=self.world.stockist, variant=self.variant, quantity=10)
        order = self.checkout(3)
        item = OrderItem.objects.get(order=order)
        self.assertEqual(item.stock_source, 'stockist')
        inv.refresh_from_db()
        self.assertEqual(inv.quantity, 7)
        self.assertEqual(self.supplier_stock(), 100)

    def test_distributor_stock_is_used_before_stockist(self):
        WholesalerStock.objects.create(
            wholesaler=self.world.wholesaler, variant=self.variant, quantity=5)
        StockistInventory.objects.create(
            stockist=self.world.stockist, variant=self.variant, quantity=10)
        order = self.checkout(3)
        self.assertEqual(OrderItem.objects.get(order=order).stock_source, 'wholesaler')
        self.assertEqual(WholesalerStock.objects.get().quantity, 2)
        self.assertEqual(StockistInventory.objects.get().quantity, 10)

    def test_blocked_distributor_packs_are_skipped(self):
        WholesalerStock.objects.create(
            wholesaler=self.world.wholesaler, variant=self.variant,
            quantity=5, blocked_quantity=4)
        StockistInventory.objects.create(
            stockist=self.world.stockist, variant=self.variant, quantity=10)
        order = self.checkout(3)
        self.assertEqual(OrderItem.objects.get(order=order).stock_source, 'stockist')
        self.assertEqual(WholesalerStock.objects.get().quantity, 5)

    def test_supplier_is_used_when_no_upper_tier_covers_the_line(self):
        StockistInventory.objects.create(
            stockist=self.world.stockist, variant=self.variant, quantity=2)
        order = self.checkout(3)
        self.assertEqual(OrderItem.objects.get(order=order).stock_source, 'supplier')
        self.assertEqual(self.supplier_stock(), 97)

    def test_customer_removing_the_item_returns_packs_to_the_stockist(self):
        inv = StockistInventory.objects.create(
            stockist=self.world.stockist, variant=self.variant, quantity=10)
        order = self.checkout(3)
        item = OrderItem.objects.get(order=order)
        self.client_c.post(reverse('orders:order_edit', args=[order.pk]),
                           {'action': 'remove_item', 'item_id': item.pk})
        inv.refresh_from_db()
        self.assertEqual(inv.quantity, 10)
        self.assertEqual(self.supplier_stock(), 100)


class ShopRequestFlowTests(StockFlowBase):
    def _shop_request(self, qty):
        req = RestockRequest.objects.create(
            distributor=self.shop, variant=self.variant,
            quantity_requested=qty, loose_grams=0, status='awaiting_stock')
        block_for_shop_request(req)
        return req

    def test_distributor_fills_shop_request_from_its_stock(self):
        wh = WholesalerStock.objects.create(
            wholesaler=self.world.wholesaler, variant=self.variant, quantity=10)
        req = self._shop_request(4)
        wh.refresh_from_db()
        self.assertEqual(wh.blocked_quantity, 4)
        client_w = login_as(self.client_class(), self.world.wholesaler_user)
        client_w.post(reverse('wholesaler:my_stock'),
                      {'action': 'fulfil_marketplace_packed', 'request_id': req.pk})
        wh.refresh_from_db()
        req.refresh_from_db()
        self.assertEqual(wh.quantity, 6)
        self.assertEqual(wh.blocked_quantity, 0)
        self.assertEqual(req.status, 'delivered')
        shop = DistributorStock.objects.get(distributor=self.shop, variant=self.variant)
        self.assertEqual(shop.quantity, 4)

    def test_distributor_forwards_shop_request_to_stockist(self):
        wh = WholesalerStock.objects.create(
            wholesaler=self.world.wholesaler, variant=self.variant, quantity=0)
        StockistInventory.objects.create(
            stockist=self.world.stockist, variant=self.variant, quantity=10)
        req = self._shop_request(4)
        client_w = login_as(self.client_class(), self.world.wholesaler_user)
        client_w.post(reverse('wholesaler:my_stock'),
                      {'action': 'forward_marketplace_packed', 'request_id': req.pk})
        req.refresh_from_db()
        self.assertFalse(req.wholesaler_blocked)
        self.assertEqual(StockistInventory.objects.get().blocked_quantity, 4)
        self.assertEqual(req.status, 'awaiting_stock')
