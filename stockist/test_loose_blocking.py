"""Loose (weighed) stock is blocked at the stockist and at the distributor (kb/13)."""
from django.test import TestCase

from accounts.factories import build_world
from distributors.models import RestockRequest
from distributors.shop_blocking import block_for_shop_request, release_shop_request_block
from distributors.wholesaler_models import WholesalerLooseStock
from stockist.models import StockistLooseStock
from stockist.supply_requests import RequestBlocked, block_loose, release_loose


class StockistLooseBlockingTests(TestCase):
    def setUp(self):
        self.world = build_world()
        self.product = self.world.shared_product
        self.loose = StockistLooseStock.objects.create(
            stockist=self.world.stockist, product=self.product, quantity_grams=1000)

    def test_block_then_refuse_when_free_grams_are_short(self):
        block_loose(self.world.stockist, self.product, 600)
        with self.assertRaises(RequestBlocked):
            block_loose(self.world.stockist, self.product, 500)
        self.loose.refresh_from_db()
        self.assertEqual(self.loose.blocked_grams, 600)
        self.assertEqual(self.loose.free_grams, 400)

    def test_release_frees_the_grams(self):
        block_loose(self.world.stockist, self.product, 600)
        release_loose(self.world.stockist, self.product, 600)
        self.loose.refresh_from_db()
        self.assertEqual(self.loose.blocked_grams, 0)


class ShopLooseBlockingTests(TestCase):
    def setUp(self):
        self.world = build_world()
        self.shop = self.world.shop_a
        self.product = self.world.shared_product
        self.stock = WholesalerLooseStock.objects.create(
            wholesaler=self.world.wholesaler, product=self.product, quantity_grams=2000)

    def _loose_request(self, grams):
        return RestockRequest.objects.create(
            distributor=self.shop, variant=self.world.shared_variant,
            quantity_requested=1, loose_grams=grams, status='awaiting_stock')

    def test_shop_loose_request_blocks_distributor_grams(self):
        req = self._loose_request(500)
        self.assertTrue(block_for_shop_request(req))
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.blocked_grams, 500)
        self.assertEqual(self.stock.free_grams, 1500)

    def test_shop_loose_request_refused_when_free_grams_are_short(self):
        req = self._loose_request(3000)
        self.assertFalse(block_for_shop_request(req))
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.blocked_grams, 0)

    def test_release_gives_the_grams_back(self):
        req = self._loose_request(500)
        block_for_shop_request(req)
        release_shop_request_block(req)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.blocked_grams, 0)
