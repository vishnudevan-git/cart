"""Nearest-first stock (kb/13): Distributor, then Stockist, then supplier; distributor-to-shop
blocking; customer hide rule."""
from django.test import TestCase

from accounts.factories import build_world
from distributors.models import RestockRequest
from distributors.shop_blocking import block_for_shop_request, release_shop_request_block
from distributors.wholesaler_models import WholesalerStock
from orders.upstream_stock import consume_upper, pick_upper_tier, return_upper, tier_free
from products.models import ProductVariant
from stockist.models import StockistInventory


class NearestFirstTests(TestCase):
    def setUp(self):
        self.world = build_world()
        self.shop = self.world.shop_a
        self.world.wholesaler.stockist = self.world.stockist
        self.world.wholesaler.save(update_fields=['stockist'])
        self.variant = self.world.shared_variant
        ProductVariant.objects.filter(pk=self.variant.pk).update(stock=50)
        self.variant.refresh_from_db()

    def _wholesaler_stock(self, quantity, blocked=0):
        return WholesalerStock.objects.create(
            wholesaler=self.world.wholesaler, variant=self.variant,
            quantity=quantity, blocked_quantity=blocked)

    def _stockist_stock(self, quantity):
        return StockistInventory.objects.create(
            stockist=self.world.stockist, variant=self.variant, quantity=quantity)

    def test_distributor_is_used_first_when_it_can_cover_the_line(self):
        self._wholesaler_stock(5)
        self._stockist_stock(10)
        self.assertEqual(pick_upper_tier(self.shop, self.variant, 3), 'wholesaler')

    def test_stockist_is_used_when_distributor_cannot_cover_the_whole_line(self):
        self._wholesaler_stock(5)
        self._stockist_stock(10)
        self.assertEqual(pick_upper_tier(self.shop, self.variant, 6), 'stockist')

    def test_blocked_distributor_packs_are_not_free(self):
        self._wholesaler_stock(5, blocked=4)
        self._stockist_stock(10)
        self.assertEqual(tier_free(self.shop, self.variant, 'wholesaler'), 1)
        self.assertEqual(pick_upper_tier(self.shop, self.variant, 3), 'stockist')

    def test_no_upper_tier_gives_none_so_supplier_path_decides(self):
        self.assertIsNone(pick_upper_tier(self.shop, self.variant, 3))

    def test_consume_then_return_puts_packs_back_at_the_same_tier(self):
        stock = self._wholesaler_stock(5)
        consume_upper('wholesaler', self.shop, self.variant, 3)
        stock.refresh_from_db()
        self.assertEqual(stock.quantity, 2)
        return_upper('wholesaler', self.shop, self.variant, 3)
        stock.refresh_from_db()
        self.assertEqual(stock.quantity, 5)

    def test_stockist_consume_and_return(self):
        inv = self._stockist_stock(10)
        consume_upper('stockist', self.shop, self.variant, 4)
        inv.refresh_from_db()
        self.assertEqual(inv.quantity, 6)
        return_upper('stockist', self.shop, self.variant, 4)
        inv.refresh_from_db()
        self.assertEqual(inv.quantity, 10)


class ShopRequestBlockingTests(TestCase):
    def setUp(self):
        self.world = build_world()
        self.shop = self.world.shop_a
        self.variant = self.world.shared_variant
        self.stock = WholesalerStock.objects.create(
            wholesaler=self.world.wholesaler, variant=self.variant, quantity=10)

    def _request(self, qty):
        return RestockRequest.objects.create(
            distributor=self.shop, variant=self.variant,
            quantity_requested=qty, loose_grams=0, status='awaiting_stock')

    def test_shop_request_blocks_distributor_packs_at_once(self):
        req = self._request(4)
        self.assertTrue(block_for_shop_request(req))
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.blocked_quantity, 4)
        self.assertEqual(self.stock.free_quantity, 6)

    def test_second_shop_request_is_not_blocked_when_free_packs_are_short(self):
        block_for_shop_request(self._request(8))
        second = self._request(5)
        self.assertFalse(block_for_shop_request(second))
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.blocked_quantity, 8)

    def test_releasing_the_block_frees_the_packs(self):
        req = self._request(4)
        block_for_shop_request(req)
        release_shop_request_block(req)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.blocked_quantity, 0)
        req.refresh_from_db()
        self.assertFalse(req.wholesaler_blocked)


class CustomerHideRuleTests(TestCase):
    def setUp(self):
        self.world = build_world()
        self.shop = self.world.shop_a
        self.world.wholesaler.stockist = self.world.stockist
        self.world.wholesaler.save(update_fields=['stockist'])
        self.variant = self.world.shared_variant
        ProductVariant.objects.filter(pk=self.variant.pk).update(stock=0)

    def test_product_with_no_stock_anywhere_is_hidden(self):
        from products.views import _orderable
        variant = ProductVariant.objects.get(pk=self.variant.pk)
        self.assertFalse(_orderable(variant, self.shop))

    def test_product_stays_visible_when_distributor_has_free_stock(self):
        from products.views import _orderable
        WholesalerStock.objects.create(
            wholesaler=self.world.wholesaler, variant=self.variant, quantity=3)
        variant = ProductVariant.objects.get(pk=self.variant.pk)
        self.assertTrue(_orderable(variant, self.shop))
