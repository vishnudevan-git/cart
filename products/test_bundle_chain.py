"""Bundle rules across the chain (kb/10-bundle-supply-chain.md).

Chain under test: Supplier -> Stockist -> Distributor (wholesaler) -> shop.
A bundle is a count: bundle size 10 means orders in steps of 10 units.
"""
from django.test import TestCase
from django.urls import reverse

from accounts.factories import build_world, login_as
from distributors.models import DistributorProduct, DistributorStock, RestockRequest
from distributors.wholesaler_models import WholesalerRestockRequest, WholesalerStock
from products.bundle_chain import effective_seller, required_multiple, rule_mode
from products.models import ProductBundle, ProductVariant, TierBundleRule


class BundleChainTestCase(TestCase):
    def setUp(self):
        self.world = build_world()
        self.world.wholesaler.stockist = self.world.stockist
        self.world.wholesaler.save(update_fields=['stockist'])
        self.product = self.world.shared_product
        self.variant = self.world.shared_variant
        ProductBundle.objects.create(
            product=self.product, variant=self.variant, name='Case of 10', pack_quantity=10,
        )
        DistributorProduct.objects.create(
            distributor=self.world.shop_a, product=self.product, is_listed=True,
        )
        # An active price marks the shop as set up (Global Pricing gate).
        from distributors.models import DistributorVariantPrice
        DistributorVariantPrice.objects.create(
            distributor=self.world.shop_a, variant=self.variant,
            base_price=500, selling_price=550, is_active=True,
        )

    def rule(self, seller, mode, scope='global', **target):
        tier = 'stockist' if seller is self.world.stockist else 'wholesaler'
        return TierBundleRule.objects.create(
            tier=tier, owner_id=seller.pk, scope=scope, mode=mode, **target,
        )

    def supplier_bundle_only(self, on=True):
        ProductVariant.objects.filter(pk=self.variant.pk).update(only_bundle=on)
        self.variant.refresh_from_db()

    def no_buffer(self, on=True):
        self.world.wholesaler.no_buffer_stock = on
        self.world.wholesaler.save(update_fields=['no_buffer_stock'])
        self.world.shop_a.refresh_from_db()


class RuleResolutionTests(BundleChainTestCase):
    def test_default_is_singles(self):
        self.assertEqual(rule_mode(self.world.stockist, self.product), 'singles')
        self.assertEqual(required_multiple(self.world.wholesaler, self.variant), 1)

    def test_product_beats_supplier_beats_global(self):
        s = self.world.stockist
        self.rule(s, 'bundle_only')
        self.assertEqual(rule_mode(s, self.product), 'bundle_only')
        self.rule(s, 'singles', scope='supplier', supplier=self.world.supplier)
        self.assertEqual(rule_mode(s, self.product), 'singles')
        self.rule(s, 'bundle_only', scope='product', product=self.product)
        self.assertEqual(rule_mode(s, self.product), 'bundle_only')

    def test_stockist_may_break_a_supplier_bundle(self):
        self.supplier_bundle_only()
        # Stockist buys from the supplier in whole bundles only.
        self.assertEqual(required_multiple(self.world.stockist, self.variant), 10)
        # By default it sells singles downstream.
        self.assertEqual(required_multiple(self.world.wholesaler, self.variant), 1)

    def test_forward_follows_the_supplier(self):
        self.rule(self.world.stockist, 'forward')
        self.assertEqual(required_multiple(self.world.wholesaler, self.variant), 1)
        self.supplier_bundle_only()
        self.assertEqual(required_multiple(self.world.wholesaler, self.variant), 10)

    def test_distributor_bundle_only_binds_its_shops(self):
        self.rule(self.world.wholesaler, 'bundle_only')
        self.assertEqual(required_multiple(self.world.shop_a, self.variant), 10)

    def test_no_buffer_distributor_is_skipped(self):
        self.rule(self.world.wholesaler, 'bundle_only')
        self.no_buffer()
        self.assertEqual(effective_seller(self.world.shop_a), self.world.stockist)
        self.assertEqual(required_multiple(self.world.shop_a, self.variant), 1)
        self.rule(self.world.stockist, 'bundle_only')
        self.assertEqual(required_multiple(self.world.shop_a, self.variant), 10)


class MarketplaceRestockRoutingTests(BundleChainTestCase):
    def setUp(self):
        super().setUp()
        self.shop_client = login_as(self.client, self.world.shop_a_user)
        self.url = reverse('distributors:my_stock')

    def order(self, qty):
        self.shop_client.post(self.url, {'action': 'restock', f'qty_{self.variant.pk}': str(qty)})
        return RestockRequest.objects.filter(distributor=self.world.shop_a).order_by('-pk').first()

    def test_request_waits_at_distributor_with_buffer(self):
        self.assertEqual(self.order(3).status, 'awaiting_stock')

    def test_request_goes_to_stockist_without_buffer(self):
        self.no_buffer()
        self.assertEqual(self.order(3).status, 'pending')

    def test_bundle_only_distributor_rejects_singles(self):
        self.rule(self.world.wholesaler, 'bundle_only')
        self.assertIsNone(self.order(3))
        self.assertEqual(self.order(10).quantity_requested, 10)

    def order_loose(self, amount='1'):
        self.product.allow_loose_distribution = True
        self.product.save(update_fields=['allow_loose_distribution'])
        self.shop_client.post(self.url, {
            'action': 'restock', 'restock_type': 'loose',
            'product_id': self.product.pk, 'loose_quantity': amount,
        })
        return RestockRequest.objects.filter(
            distributor=self.world.shop_a, loose_grams__gt=0,
        ).order_by('-pk').first()

    def test_loose_request_waits_at_distributor_with_buffer(self):
        self.assertEqual(self.order_loose().status, 'awaiting_stock')

    def test_loose_request_goes_to_stockist_without_buffer(self):
        self.no_buffer()
        req = self.order_loose()
        self.assertEqual(req.status, 'pending')
        stockist_client = login_as(self.client_class(), self.world.stockist_user)
        page = stockist_client.get(reverse('stockist:distributor_restock'))
        self.assertIn(req, list(page.context['requests']))

    def test_stockist_does_not_see_requests_waiting_at_distributor(self):
        req = self.order(3)
        stockist_client = login_as(self.client_class(), self.world.stockist_user)
        page = stockist_client.get(reverse('stockist:distributor_restock'))
        self.assertNotIn(req, list(page.context['requests']))


class DistributorFillsShopRequestTests(BundleChainTestCase):
    def setUp(self):
        super().setUp()
        # The stockist must hold packs before a distributor request is accepted.
        from stockist.models import StockistInventory
        StockistInventory.objects.create(stockist=self.world.stockist, variant=self.variant, quantity=100)
        self.shop_request = RestockRequest.objects.create(
            distributor=self.world.shop_a, variant=self.variant,
            quantity_requested=3, status='awaiting_stock',
        )
        self.w_client = login_as(self.client, self.world.wholesaler_user)
        self.url = reverse('wholesaler:my_stock')

    def test_fill_from_own_stock(self):
        own = WholesalerStock.objects.create(
            wholesaler=self.world.wholesaler, variant=self.variant, quantity=12,
        )
        self.w_client.post(self.url, {
            'action': 'fulfil_marketplace_packed', 'request_id': self.shop_request.pk,
        })
        own.refresh_from_db()
        self.shop_request.refresh_from_db()
        self.assertEqual(own.quantity, 9)
        self.assertEqual(self.shop_request.status, 'delivered')
        shop_stock = DistributorStock.objects.get(distributor=self.world.shop_a, variant=self.variant)
        self.assertEqual(shop_stock.quantity, 3)

    def test_fill_refused_when_stock_is_short(self):
        self.w_client.post(self.url, {
            'action': 'fulfil_marketplace_packed', 'request_id': self.shop_request.pk,
        })
        self.shop_request.refresh_from_db()
        self.assertEqual(self.shop_request.status, 'awaiting_stock')

    def test_forward_rounds_up_to_stockist_bundle(self):
        self.rule(self.world.stockist, 'bundle_only')
        self.w_client.post(self.url, {
            'action': 'forward_marketplace_packed', 'request_id': self.shop_request.pk,
        })
        upstream = WholesalerRestockRequest.objects.get(marketplace_request=self.shop_request)
        self.assertEqual(upstream.quantity_requested, 10)
        self.assertEqual(upstream.stockist, self.world.stockist)

    def test_forward_only_once(self):
        for _ in range(2):
            self.w_client.post(self.url, {
                'action': 'forward_marketplace_packed', 'request_id': self.shop_request.pk,
            })
        self.assertEqual(
            WholesalerRestockRequest.objects.filter(marketplace_request=self.shop_request).count(), 1,
        )

    def test_page_lists_waiting_shop_request(self):
        page = self.w_client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'No buffer stock')
        self.assertContains(page, 'Request from stockist')
        self.assertContains(page, self.world.shop_a.company_name)

    def test_no_buffer_toggle(self):
        self.w_client.post(self.url, {'action': 'buffer_setting', 'no_buffer_stock': '1'})
        self.world.wholesaler.refresh_from_db()
        self.assertTrue(self.world.wholesaler.no_buffer_stock)
        self.w_client.post(self.url, {'action': 'buffer_setting'})
        self.world.wholesaler.refresh_from_db()
        self.assertFalse(self.world.wholesaler.no_buffer_stock)


class BundleSettingsPageTests(BundleChainTestCase):
    def test_stockist_sets_and_removes_rules(self):
        c = login_as(self.client, self.world.stockist_user)
        url = reverse('stockist:bundle_settings')
        self.assertEqual(c.get(url).status_code, 200)
        c.post(url, {'bundle_action': 'set_global', 'mode': 'bundle_only'})
        c.post(url, {'bundle_action': 'set_product', 'mode': 'singles',
                     'product_id': self.product.pk})
        self.assertEqual(rule_mode(self.world.stockist, self.product), 'singles')
        row = TierBundleRule.objects.get(scope='product', owner_id=self.world.stockist.pk)
        c.post(url, {'bundle_action': 'remove', 'rule_id': row.pk})
        self.assertEqual(rule_mode(self.world.stockist, self.product), 'bundle_only')

    def test_global_cannot_be_removed_and_mode_is_validated(self):
        c = login_as(self.client, self.world.stockist_user)
        url = reverse('stockist:bundle_settings')
        c.post(url, {'bundle_action': 'set_global', 'mode': 'nonsense'})
        self.assertFalse(TierBundleRule.objects.exists())
        c.post(url, {'bundle_action': 'set_global', 'mode': 'forward'})
        row = TierBundleRule.objects.get(scope='global')
        c.post(url, {'bundle_action': 'remove', 'rule_id': row.pk})
        self.assertTrue(TierBundleRule.objects.filter(pk=row.pk).exists())

    def test_distributor_cannot_edit_another_tiers_rule(self):
        stockist_row = self.rule(self.world.stockist, 'bundle_only', scope='product',
                                 product=self.product)
        c = login_as(self.client, self.world.wholesaler_user)
        url = reverse('wholesaler:bundle_settings')
        self.assertEqual(c.get(url).status_code, 200)
        c.post(url, {'bundle_action': 'remove', 'rule_id': stockist_row.pk})
        self.assertTrue(TierBundleRule.objects.filter(pk=stockist_row.pk).exists())


class AutoRestockRoutingTests(BundleChainTestCase):
    def test_auto_request_waits_at_distributor_and_uses_its_step(self):
        from stockist.auto_restock import check_marketplace
        shop = self.world.shop_a
        shop.auto_restock_enabled = True
        shop.auto_restock_threshold = 5
        shop.auto_restock_quantity = 3
        shop.save()
        DistributorStock.objects.create(distributor=shop, variant=self.variant, quantity=0)
        self.rule(self.world.wholesaler, 'bundle_only')
        self.assertEqual(check_marketplace(shop), 1)
        req = RestockRequest.objects.get(distributor=shop)
        self.assertEqual(req.status, 'awaiting_stock')
        self.assertEqual(req.quantity_requested, 10)
