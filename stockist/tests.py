"""
Stage 7 — per-tier stock rules, stockist routing and direct supply.

The rules under test (PROJECT_NOTES "Stage 7"):

* Every tier that holds stock (stockist, wholesale Distributor, Marketplace)
  may manually REDUCE its stock (offline sales, own use, wastage) but may
  NEVER manually increase it — increases arrive only through a delivered
  restock order. Stock cannot be typed into existence.
* The two sources are the exception: a supplier sets its stock freely, and a
  Marketplace sets its OWN products' stock freely (it is their local supplier).
* One stockist serves many Distributors, but a Distributor buys from exactly
  ONE stockist — requests route only to the assigned stockist.
* Direct-supply products (e.g. eggs) go supplier → marketplace: no stockist
  charges, no Distributor margin — no commissions for either.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.factories import build_world, make_user, login_as
from products.models import ProductVariant
from stockist.models import (Stockist, StockistInventory, StockistRestockRequest,
                             get_stockist_price, stockist_for)


class StockRulesTestCase(TestCase):
    def setUp(self):
        self.world = build_world()
        # Assign the chain: shop A/B's Distributor buys from THE stockist.
        self.world.wholesaler.stockist = self.world.stockist
        self.world.wholesaler.save(update_fields=['stockist'])
        self.variant = self.world.shared_variant


class StockistManualRulesTests(StockRulesTestCase):
    """Stockist inventory: reduce manually, never increase manually."""

    def setUp(self):
        super().setUp()
        self.inv = StockistInventory.objects.create(
            stockist=self.world.stockist, variant=self.variant, quantity=5
        )
        self.client_s = login_as(self.client, self.world.stockist_user)

    def test_manual_increase_is_blocked(self):
        self.client_s.post(reverse('stockist:inventory'), {f'qty_{self.variant.pk}': '10'})
        self.inv.refresh_from_db()

        self.assertEqual(self.inv.quantity, 5, 'stock was typed into existence')

    def test_manual_decrease_works(self):
        self.client_s.post(reverse('stockist:inventory'), {f'qty_{self.variant.pk}': '3'})
        self.inv.refresh_from_db()

        self.assertEqual(self.inv.quantity, 3)

    def test_supplier_fulfilment_is_the_increase_path(self):
        """Supplier fulfils → stockist inventory up, supplier stock down."""
        req = StockistRestockRequest.objects.create(
            stockist=self.world.stockist, variant=self.variant, quantity_requested=20
        )
        supplier_client = login_as(self.client_class(), self.world.supplier_user)

        supplier_client.post(reverse('suppliers:restock_requests'),
                             {'kind': 'stockist', 'req_id': req.pk, 'action': 'fulfil'})

        self.inv.refresh_from_db()
        self.variant.refresh_from_db()
        req.refresh_from_db()
        self.assertEqual(self.inv.quantity, 25)          # 5 + 20
        self.assertEqual(self.variant.stock, 80)         # 100 - 20
        self.assertEqual(req.status, 'fulfilled')

    def test_fulfilment_blocked_when_supplier_lacks_stock(self):
        req = StockistRestockRequest.objects.create(
            stockist=self.world.stockist, variant=self.variant, quantity_requested=999
        )
        supplier_client = login_as(self.client_class(), self.world.supplier_user)

        supplier_client.post(reverse('suppliers:restock_requests'),
                             {'kind': 'stockist', 'req_id': req.pk, 'action': 'fulfil'})

        req.refresh_from_db()
        self.inv.refresh_from_db()
        self.assertEqual(req.status, 'pending')
        self.assertEqual(self.inv.quantity, 5)


class WholesalerStockTests(StockRulesTestCase):
    """The wholesale Distributor's ledger: order-in, reduce-only manual."""

    def setUp(self):
        super().setUp()
        self.client_w = login_as(self.client, self.world.wholesaler_user)

    def _request_stock(self, qty=4):
        self.client_w.post(reverse('wholesaler:my_stock'),
                           {'action': 'restock', f'qty_{self.variant.pk}': str(qty)})
        from distributors.wholesaler_models import WholesalerRestockRequest
        return WholesalerRestockRequest.objects.get(wholesaler=self.world.wholesaler)

    def test_restock_request_goes_to_the_assigned_stockist_only(self):
        req = self._request_stock()

        self.assertEqual(req.stockist, self.world.stockist)
        self.assertEqual(req.status, 'pending')

    def test_stockist_delivery_increases_stock(self):
        from distributors.wholesaler_models import WholesalerStock
        req = self._request_stock(4)
        stockist_client = login_as(self.client_class(), self.world.stockist_user)

        stockist_client.post(reverse('stockist:distributor_restock'),
                             {'kind': 'wholesaler', 'req_id': req.pk, 'action': 'deliver'})

        stock = WholesalerStock.objects.get(wholesaler=self.world.wholesaler, variant=self.variant)
        self.assertEqual(stock.quantity, 4)

    def test_manual_reduce_works_but_never_beyond_stock(self):
        from distributors.wholesaler_models import WholesalerStock
        stock = WholesalerStock.objects.create(
            wholesaler=self.world.wholesaler, variant=self.variant, quantity=3
        )

        self.client_w.post(reverse('wholesaler:my_stock'),
                           {'action': 'reduce', 'stock_id': stock.pk, 'units': '1'})
        stock.refresh_from_db()
        self.assertEqual(stock.quantity, 2)

        self.client_w.post(reverse('wholesaler:my_stock'),
                           {'action': 'reduce', 'stock_id': stock.pk, 'units': '99'})
        stock.refresh_from_db()
        self.assertEqual(stock.quantity, 2, 'over-reduce must be rejected')

    def test_no_assigned_stockist_blocks_requests(self):
        from distributors.wholesaler_models import WholesalerRestockRequest
        self.world.wholesaler.stockist = None
        self.world.wholesaler.save(update_fields=['stockist'])

        self.client_w.post(reverse('wholesaler:my_stock'),
                           {'action': 'restock', f'qty_{self.variant.pk}': '4'})

        self.assertFalse(WholesalerRestockRequest.objects.exists())


class MarketplaceStockRulesTests(StockRulesTestCase):
    """Shop stock: reduce-only for chain products, free for own products."""

    def setUp(self):
        super().setUp()
        from distributors.models import (DistributorProduct, DistributorStock,
                                         DistributorStockBatch)
        from distributors.models import DistributorVariantPrice
        DistributorProduct.objects.create(
            distributor=self.world.shop_a, product=self.world.shared_product, is_listed=True
        )
        # An active price marks the shop as set up (Global Pricing gate).
        DistributorVariantPrice.objects.create(
            distributor=self.world.shop_a, variant=self.variant,
            base_price=500, selling_price=550, is_active=True,
        )
        self.stock = DistributorStock.objects.create(
            distributor=self.world.shop_a, variant=self.variant, quantity=0
        )
        DistributorStockBatch.objects.create(stock=self.stock, quantity=10, cost_price=500)
        self.stock.refresh_quantity()
        self.client_a = login_as(self.client, self.world.shop_a_user)

    def test_manual_reduce_consumes_fifo(self):
        self.client_a.post(reverse('distributors:my_stock'),
                           {'action': 'reduce', 'variant_id': self.variant.pk, 'units': '4'})
        self.stock.refresh_from_db()

        self.assertEqual(self.stock.quantity, 6)

    def test_reduce_beyond_stock_is_rejected(self):
        self.client_a.post(reverse('distributors:my_stock'),
                           {'action': 'reduce', 'variant_id': self.variant.pk, 'units': '99'})
        self.stock.refresh_from_db()

        self.assertEqual(self.stock.quantity, 10)

    def test_no_manual_increase_path_exists(self):
        """The old direct loose-add is gone: a loose restock now only creates a
        PENDING request — stock rises when the stockist delivers, not before."""
        from distributors.models import RestockRequest, DistributorLooseStock
        from products.models import Product
        Product.all_objects.filter(pk=self.world.shared_product.pk).update(
            allow_loose_distribution=True
        )

        self.client_a.post(reverse('distributors:my_stock'),
                           {'action': 'restock', 'restock_type': 'loose',
                            'product_id': self.world.shared_product.pk, 'loose_kg': '2'})

        req = RestockRequest.objects.get(distributor=self.world.shop_a)
        self.assertEqual(req.loose_grams, 2000)
        self.assertEqual(req.status, 'pending')
        self.assertFalse(
            DistributorLooseStock.objects.filter(distributor=self.world.shop_a).exists(),
            'loose stock appeared without a delivery'
        )

        # Stockist delivers → NOW it lands in the loose pool.
        stockist_client = login_as(self.client_class(), self.world.stockist_user)
        stockist_client.post(reverse('stockist:distributor_restock'),
                             {'req_id': req.pk, 'action': 'deliver'})
        loose = DistributorLooseStock.objects.get(distributor=self.world.shop_a)
        self.assertEqual(loose.quantity_grams, 2000)

        # And loose can be reduced manually (offline sale).
        self.client_a.post(reverse('distributors:my_stock'),
                           {'action': 'reduce_loose',
                            'product_id': self.world.shared_product.pk, 'kg': '0.5'})
        loose.refresh_from_db()
        self.assertEqual(loose.quantity_grams, 1500)

    def test_own_product_stock_is_free_in_both_directions(self):
        """The shop is its own products' source — a local supplier."""
        client = self.client_a
        client.post(reverse('distributors:own_product_create'), {
            'name': 'Amma Pickle', 'category': self.world.category.pk,
            'variant_id': [''], 'variant_label': ['250 g'], 'variant_price': ['120'],
            'variant_mrp': [''], 'variant_stock': ['10'], 'variant_grams': ['250'],
        })
        from products.models import Product
        own = Product.all_objects.get(name='Amma Pickle')
        variant = own.variants.first()

        client.post(reverse('distributors:my_stock'),
                    {'action': 'own_stock', 'variant_id': variant.pk, 'stock': '50'})
        variant.refresh_from_db()
        self.assertEqual(variant.stock, 50)          # increase allowed

        client.post(reverse('distributors:my_stock'),
                    {'action': 'own_stock', 'variant_id': variant.pk, 'stock': '20'})
        variant.refresh_from_db()
        self.assertEqual(variant.stock, 20)          # decrease allowed

    def test_own_stock_action_rejects_chain_products(self):
        self.client_a.post(reverse('distributors:my_stock'),
                           {'action': 'own_stock', 'variant_id': self.variant.pk, 'stock': '999'})
        self.variant.refresh_from_db()

        self.assertEqual(self.variant.stock, 100, 'shop set a supplier variant stock')


class StockistRoutingTests(StockRulesTestCase):
    """One Distributor ↔ one stockist: requests route to the assigned one."""

    def setUp(self):
        super().setUp()
        stockist_b_user = make_user('stockist2@test.com', 'stockist')
        self.stockist_b = Stockist.objects.create(
            user=stockist_b_user, company_name='Second Stockist', status='active'
        )
        self.stockist_b_user = stockist_b_user

    def test_stockist_for_resolves_through_the_chain(self):
        self.assertEqual(stockist_for(self.world.shop_a), self.world.stockist)

        self.world.wholesaler.stockist = self.stockist_b
        self.world.wholesaler.save(update_fields=['stockist'])
        self.assertEqual(stockist_for(self.world.shop_a), self.stockist_b)

    def test_marketplace_request_is_invisible_to_the_other_stockist(self):
        from distributors.models import RestockRequest
        req = RestockRequest.objects.create(
            distributor=self.world.shop_a, variant=self.variant, quantity_requested=5
        )

        page_a = login_as(self.client_class(), self.world.stockist_user).get(
            reverse('stockist:distributor_restock'))
        page_b = login_as(self.client_class(), self.stockist_b_user).get(
            reverse('stockist:distributor_restock'))

        self.assertIn(self.world.shop_a.company_name, page_a.content.decode())
        self.assertNotIn(self.world.shop_a.company_name, page_b.content.decode())
        # And stockist B cannot act on it either.
        login_as(self.client_class(), self.stockist_b_user).post(
            reverse('stockist:distributor_restock'), {'req_id': req.pk, 'action': 'deliver'})
        req.refresh_from_db()
        self.assertEqual(req.status, 'pending')


class DirectSupplyTests(StockRulesTestCase):
    """Supplier → marketplace directly: no stockist, no Distributor, no commissions."""

    def setUp(self):
        super().setUp()
        from accounts.factories import make_shared_product
        from distributors.models import DistributorProduct, DistributorVariantPrice
        # Eggs: approved but NEVER charge-confirmed — the stockist never sees them.
        self.eggs = make_shared_product(
            self.world.supplier, self.world.category, name='Farm Eggs',
            price='90', stock=200, direct_supply=True, charges_confirmed=False,
        )
        self.egg_variant = self.eggs.variants.first()
        DistributorProduct.objects.create(
            distributor=self.world.shop_a, product=self.eggs, is_listed=True
        )
        DistributorVariantPrice.objects.create(
            distributor=self.world.shop_a, variant=self.egg_variant,
            base_price=Decimal('90'), selling_price=Decimal('100'), is_active=True,
        )

    def test_price_is_supplier_price_with_no_commissions(self):
        """Even with stockist charges and a Distributor margin configured,
        a direct product's cost is the supplier price alone."""
        from stockist.models import StockistStage, ProcessingCharge
        stage = StockistStage.objects.create(stockist=self.world.stockist, name='Packing')
        ProcessingCharge.objects.create(
            stockist=self.world.stockist, stage_obj=stage, scope='global',
            charge_type='fixed', charge_value=Decimal('15'), is_active=True,
        )
        self.world.wholesaler.margin_value = Decimal('10')
        self.world.wholesaler.save(update_fields=['margin_value'])

        cost, breakdown = get_stockist_price(
            self.eggs, self.egg_variant, self.world.stockist, enterprise=self.world.shop_a
        )
        self.assertEqual(cost, Decimal('90'))
        self.assertEqual(breakdown, [])

        # …while a normal product still collects both commissions.
        normal_cost, normal_breakdown = get_stockist_price(
            self.world.shared_product, self.variant,
            self.world.stockist, enterprise=self.world.shop_a
        )
        self.assertEqual(normal_cost, Decimal('500') + 15 + 10)
        self.assertEqual(len(normal_breakdown), 2)

    def test_visible_to_customers_without_stockist_charges(self):
        client = login_as(self.client, self.world.customer_a)

        listing = client.get(reverse('products:product_list'))
        self.assertIn('Farm Eggs', listing.content.decode())

        detail = client.get(f'/products/{self.eggs.pk}/')
        self.assertEqual(detail.status_code, 200)

    def test_excluded_from_stockist_surfaces(self):
        client = login_as(self.client, self.world.stockist_user)

        inventory = client.get(reverse('stockist:inventory'))
        self.assertNotIn('Farm Eggs', inventory.content.decode())

    def test_supplier_delivers_directly_into_marketplace_stock(self):
        from distributors.models import RestockRequest, DistributorStock
        req = RestockRequest.objects.create(
            distributor=self.world.shop_a, variant=self.egg_variant, quantity_requested=30
        )
        supplier_client = login_as(self.client, self.world.supplier_user)

        supplier_client.post(reverse('suppliers:restock_requests'),
                             {'req_id': req.pk, 'action': 'deliver_direct'})

        req.refresh_from_db()
        self.egg_variant.refresh_from_db()
        stock = DistributorStock.objects.get(
            distributor=self.world.shop_a, variant=self.egg_variant
        )
        self.assertEqual(req.status, 'delivered')
        self.assertEqual(stock.quantity, 30)
        self.assertEqual(self.egg_variant.stock, 170)    # 200 - 30

    def test_direct_requests_never_reach_a_stockist(self):
        from distributors.models import RestockRequest
        RestockRequest.objects.create(
            distributor=self.world.shop_a, variant=self.egg_variant, quantity_requested=30
        )
        page = login_as(self.client, self.world.stockist_user).get(
            reverse('stockist:distributor_restock'))

        self.assertNotIn('Farm Eggs', page.content.decode())


class StockistDirectControlTests(StockRulesTestCase):
    """The STOCKIST — not the supplier — decides what bypasses them."""

    def setUp(self):
        super().setUp()
        self.client_s = login_as(self.client, self.world.stockist_user)
        self.product = self.world.shared_product      # in chain, charges confirmed

    def test_stockist_toggles_direct_on_and_off(self):
        self.client_s.post(reverse('stockist:direct_supply'),
                           {'product_id': self.product.pk})
        self.product.refresh_from_db()
        self.assertTrue(self.product.direct_supply)

        self.client_s.post(reverse('stockist:direct_supply'),
                           {'product_id': self.product.pk})
        self.product.refresh_from_db()
        self.assertFalse(self.product.direct_supply)

    def test_direct_page_is_stockist_only(self):
        for user in (self.world.supplier_user, self.world.shop_a_user,
                     self.world.wholesaler_user, self.world.customer_a):
            with self.subTest(user=user.username):
                client = login_as(self.client_class(), user)
                response = client.post(reverse('stockist:direct_supply'),
                                       {'product_id': self.product.pk})
                self.assertEqual(response.status_code, 302)
                self.product.refresh_from_db()
                self.assertFalse(self.product.direct_supply)

    def test_supplier_form_can_no_longer_set_it(self):
        """The supplier's product-edit POST must ignore any direct_supply value."""
        client = login_as(self.client, self.world.supplier_user)

        client.post(reverse('suppliers:product_edit', args=[self.product.pk]), {
            'name': self.product.name,
            'category': self.world.category.pk,
            'stock_amount': '100', 'stock_unit': 'g', 'price_per_kg': '1',
            'direct_supply': '1',                      # smuggled — must be ignored
            'variants-TOTAL_FORMS': '0', 'variants-INITIAL_FORMS': '0',
            'variants-MIN_NUM_FORMS': '0', 'variants-MAX_NUM_FORMS': '1000',
        })

        self.product.refresh_from_db()
        self.assertFalse(self.product.direct_supply)


class AutoRestockTests(StockRulesTestCase):
    """Stage 7g: low stock files the restock REQUEST automatically — it never
    adds stock directly, so increase-only-by-order still holds."""

    def setUp(self):
        super().setUp()
        from distributors.models import (DistributorProduct, DistributorStock,
                                         DistributorVariantPrice)
        # Shop A sells the shared product and is running low (2 ≤ threshold 5).
        DistributorProduct.objects.create(
            distributor=self.world.shop_a, product=self.world.shared_product, is_listed=True
        )
        DistributorVariantPrice.objects.create(
            distributor=self.world.shop_a, variant=self.variant,
            base_price=500, selling_price=550, is_active=True,
        )
        self.shop_stock = DistributorStock.objects.create(
            distributor=self.world.shop_a, variant=self.variant, quantity=2
        )

    def test_disabled_by_default_files_nothing(self):
        from stockist.auto_restock import run_all

        totals = run_all()

        self.assertEqual(sum(totals.values()), 0)

    def test_stockist_low_inventory_files_supplier_request(self):
        from stockist.auto_restock import check_stockist
        StockistInventory.objects.create(
            stockist=self.world.stockist, variant=self.variant, quantity=3
        )
        self.world.stockist.auto_restock_enabled = True
        self.world.stockist.auto_restock_quantity = 25
        self.world.stockist.save()

        self.assertEqual(check_stockist(self.world.stockist), 1)
        req = StockistRestockRequest.objects.get(stockist=self.world.stockist)
        self.assertEqual(req.quantity_requested, 25)
        self.assertEqual(req.note, 'Auto-restock (low stock)')
        # Idempotent: while one request is open, no duplicates are filed.
        self.assertEqual(check_stockist(self.world.stockist), 0)

    def test_wholesaler_low_stock_files_request_with_its_stockist(self):
        from distributors.wholesaler_models import WholesalerStock, WholesalerRestockRequest
        from stockist.auto_restock import check_wholesaler
        WholesalerStock.objects.create(
            wholesaler=self.world.wholesaler, variant=self.variant, quantity=1
        )
        self.world.wholesaler.auto_restock_enabled = True
        self.world.wholesaler.save()

        self.assertEqual(check_wholesaler(self.world.wholesaler), 1)
        req = WholesalerRestockRequest.objects.get(wholesaler=self.world.wholesaler)
        self.assertEqual(req.stockist, self.world.stockist)

        # Without an assigned stockist there is nobody to order from → no-op.
        WholesalerRestockRequest.objects.all().delete()
        self.world.wholesaler.stockist = None
        self.world.wholesaler.save(update_fields=['stockist'])
        self.assertEqual(check_wholesaler(self.world.wholesaler), 0)

    def test_marketplace_manual_reduce_triggers_auto_request(self):
        from distributors.models import RestockRequest
        self.world.shop_a.auto_restock_enabled = True
        self.world.shop_a.save()
        client = login_as(self.client, self.world.shop_a_user)

        client.post(reverse('distributors:my_stock'),
                    {'action': 'reduce', 'variant_id': self.variant.pk, 'units': '1'})

        req = RestockRequest.objects.get(distributor=self.world.shop_a)
        self.assertEqual(req.note, 'Auto-restock (low stock)')
        self.assertEqual(req.quantity_requested, self.world.shop_a.auto_restock_quantity)

    def test_own_products_are_never_auto_requested(self):
        """The shop is its own products' source — nothing to order."""
        from distributors.models import DistributorStock, RestockRequest
        from stockist.auto_restock import check_marketplace
        client = login_as(self.client, self.world.shop_a_user)
        client.post(reverse('distributors:own_product_create'), {
            'name': 'Amma Pickle', 'category': self.world.category.pk,
            'variant_id': [''], 'variant_label': ['250 g'], 'variant_price': ['120'],
            'variant_mrp': [''], 'variant_stock': ['1'], 'variant_grams': ['250'],
        })
        from products.models import Product
        own_variant = Product.all_objects.get(name='Amma Pickle').variants.first()
        DistributorStock.objects.create(
            distributor=self.world.shop_a, variant=own_variant, quantity=0
        )
        self.world.shop_a.auto_restock_enabled = True
        self.world.shop_a.save()

        check_marketplace(self.world.shop_a)

        self.assertFalse(
            RestockRequest.objects.filter(variant=own_variant).exists(),
            'auto-restock ordered a shop-own product from the chain'
        )

    def test_settings_form_saves_and_runs_immediately(self):
        from distributors.models import RestockRequest
        client = login_as(self.client, self.world.shop_a_user)

        client.post(reverse('distributors:my_stock'), {
            'action': 'auto_restock_settings', 'enabled': '1',
            'threshold': '5', 'quantity': '12',
        })

        self.world.shop_a.refresh_from_db()
        self.assertTrue(self.world.shop_a.auto_restock_enabled)
        self.assertEqual(self.world.shop_a.auto_restock_quantity, 12)
        # Stock was already low (2 ≤ 5) → the request is filed on save.
        self.assertTrue(RestockRequest.objects.filter(distributor=self.world.shop_a).exists())

    def test_management_command_sweeps_all_tiers(self):
        from io import StringIO
        from django.core.management import call_command
        self.world.shop_a.auto_restock_enabled = True
        self.world.shop_a.save()

        out = StringIO()
        call_command('auto_restock', stdout=out)

        self.assertIn('1 marketplace request(s)', out.getvalue())


class AutoRestockHighValueTests(StockRulesTestCase):
    """The high-value fix: a price cap plus a per-item off switch — the global
    auto-restock stays, but expensive items can be kept manual."""

    def setUp(self):
        super().setUp()
        # Stockist tracks the ₹500 shared variant at low quantity.
        self.inv = StockistInventory.objects.create(
            stockist=self.world.stockist, variant=self.variant, quantity=2
        )
        self.world.stockist.auto_restock_enabled = True
        self.world.stockist.save()

    def test_price_cap_skips_expensive_items(self):
        from stockist.auto_restock import check_stockist
        self.world.stockist.auto_restock_max_price = 300   # variant costs 500
        self.world.stockist.save()

        self.assertEqual(check_stockist(self.world.stockist), 0)
        self.assertFalse(StockistRestockRequest.objects.exists())

        # Raise the cap above the price → now it auto-orders.
        self.world.stockist.auto_restock_max_price = 600
        self.world.stockist.save()
        self.assertEqual(check_stockist(self.world.stockist), 1)

    def test_cap_zero_means_no_limit(self):
        from stockist.auto_restock import check_stockist
        self.world.stockist.auto_restock_max_price = 0
        self.world.stockist.save()

        self.assertEqual(check_stockist(self.world.stockist), 1)

    def test_exempt_row_is_never_auto_ordered(self):
        from stockist.auto_restock import check_stockist
        self.inv.auto_restock_exempt = True
        self.inv.save()

        self.assertEqual(check_stockist(self.world.stockist), 0)

    def test_stockist_toggle_button_flips_exemption(self):
        client = login_as(self.client, self.world.stockist_user)

        client.post(reverse('stockist:inventory'), {'toggle_auto': self.variant.pk})
        self.inv.refresh_from_db()
        self.assertTrue(self.inv.auto_restock_exempt)

        client.post(reverse('stockist:inventory'), {'toggle_auto': self.variant.pk})
        self.inv.refresh_from_db()
        self.assertFalse(self.inv.auto_restock_exempt)

    def test_marketplace_toggle_and_cap(self):
        from distributors.models import (DistributorProduct, DistributorStock,
                                         DistributorVariantPrice, RestockRequest)
        from stockist.auto_restock import check_marketplace
        DistributorProduct.objects.create(
            distributor=self.world.shop_a, product=self.world.shared_product, is_listed=True
        )
        DistributorVariantPrice.objects.create(
            distributor=self.world.shop_a, variant=self.variant,
            base_price=500, selling_price=550, is_active=True,
        )
        row = DistributorStock.objects.create(
            distributor=self.world.shop_a, variant=self.variant, quantity=1
        )
        self.world.shop_a.auto_restock_enabled = True
        self.world.shop_a.auto_restock_max_price = 300     # ₹500 item — capped out
        self.world.shop_a.save()

        self.assertEqual(check_marketplace(self.world.shop_a), 0)

        # Remove the cap but exclude the item via the toggle endpoint instead.
        self.world.shop_a.auto_restock_max_price = 0
        self.world.shop_a.save()
        client = login_as(self.client, self.world.shop_a_user)
        client.post(reverse('distributors:my_stock'),
                    {'action': 'toggle_auto', 'variant_id': self.variant.pk})
        row.refresh_from_db()
        self.assertTrue(row.auto_restock_exempt)
        self.assertEqual(check_marketplace(self.world.shop_a), 0)
        self.assertFalse(RestockRequest.objects.exists())

    def test_settings_form_saves_the_cap(self):
        client = login_as(self.client, self.world.stockist_user)

        client.post(reverse('stockist:inventory'), {
            'action': 'auto_restock_settings', 'enabled': '1',
            'threshold': '5', 'quantity': '10', 'max_price': '750.50',
        })

        self.world.stockist.refresh_from_db()
        self.assertEqual(float(self.world.stockist.auto_restock_max_price), 750.50)
