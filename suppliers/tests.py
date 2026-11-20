"""Supplier surfaces — the stock-manage category filter and the add-product form."""
import json
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.factories import build_world, make_category, make_shared_product, login_as
from products.models import Product, ProductVariant, ProductVariantType
from distributors.models import DistributorLooseStock, DistributorVariantPrice, RestockRequest
from orders.models import Order, OrderItem
from payments.models import RoleSettlement


class StockManageCategoryFilterTests(TestCase):
    def setUp(self):
        self.world = build_world()
        # A second category with its own product, same supplier.
        self.spices = make_category(name='Spices', slug='spices', code='SP')
        self.pepper = make_shared_product(
            self.world.supplier, self.spices, name='Black Pepper', price='80', stock=40
        )
        self.client_s = login_as(self.client, self.world.supplier_user)
        self.url = reverse('suppliers:stock_manage')

    def test_unfiltered_shows_everything(self):
        body = self.client_s.get(self.url).content.decode()

        self.assertIn('Marketplace Cashew', body)
        self.assertIn('Black Pepper', body)

    def test_category_filter_scopes_the_rows(self):
        body = self.client_s.get(f'{self.url}?category={self.spices.pk}').content.decode()

        self.assertIn('Black Pepper', body)
        self.assertNotIn('Marketplace Cashew', body)

    def test_save_only_touches_filtered_rows_and_keeps_filter(self):
        cashew = self.world.shared_variant                 # Dry Fruits, stock 100
        pepper = self.pepper.variants.first()              # Spices, stock 40

        response = self.client_s.post(
            f'{self.url}?category={self.spices.pk}',
            {f'stock_{pepper.pk}': '35', f'stock_{cashew.pk}': '1'},
        )

        pepper.refresh_from_db()
        cashew.refresh_from_db()
        self.assertEqual(pepper.stock, 35)
        # The cashew field was smuggled into the POST but is outside the
        # filtered queryset — it must be untouched.
        self.assertEqual(cashew.stock, 100)
        self.assertIn(f'category={self.spices.pk}', response['Location'])


class ProductEditReviewBaselineTests(TestCase):
    def setUp(self):
        self.world = build_world()
        variant_type = ProductVariantType.objects.create(
            product=self.world.shared_product, name=''
        )
        self.world.shared_variant.variant_type = variant_type
        self.world.shared_variant.save(update_fields=['variant_type'])
        self.client_s = login_as(self.client, self.world.supplier_user)

    def test_edit_page_serializes_variant_unit_price_for_change_review(self):
        response = self.client_s.get(
            reverse('suppliers:product_edit', args=[self.world.shared_product.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '"price_per_kg": 1000.0')


class SuggestedPriceTests(TestCase):
    """Goods with no printed MRP carry a *suggested* selling price so the shop
    keeps prices low. It rides the same variant formset as MRP, so guard that
    the value survives the JS → formset → DB round-trip."""

    def setUp(self):
        self.world = build_world()
        self.cat = make_category(name='Hardware', slug='hardware', code='HW')
        self.client_s = login_as(self.client, self.world.supplier_user)

    def _add(self, **over):
        data = {
            'name': 'No-Sticker Bolt',
            'category': str(self.cat.pk),
            'description': '',
            'stock_amount': '100', 'stock_unit': 'kg', 'price_per_kg': '200',
            'pricing_mode_field': 'auto',
            'show_mrp_field': 'false', 'show_suggested_field': 'true',
            'variants-TOTAL_FORMS': '1', 'variants-INITIAL_FORMS': '0',
            'variants-MIN_NUM_FORMS': '1', 'variants-MAX_NUM_FORMS': '10',
            'variants-0-label': '1kg', 'variants-0-selling_price': '200.00',
            'variants-0-mrp': '', 'variants-0-suggested_price': '240.00',
            'variants-0-stock': '100', 'variants-0-unit_grams': '1000',
            'variants-0-is_active': 'on', 'variants-0-id': '',
        }
        data.update(over)
        return self.client_s.post(reverse('suppliers:product_add'), data, follow=True)

    def test_suggested_price_saved_when_no_mrp(self):
        self._add()
        p = Product.all_objects.get(name='No-Sticker Bolt')
        self.assertTrue(p.show_suggested_price)
        self.assertFalse(p.show_mrp)
        self.assertEqual(p.variants.get().suggested_price, Decimal('240.00'))

    def test_suggested_ignored_flag_off(self):
        # Flag off → the product isn't marked as carrying a suggested price,
        # even if a stray value came through the formset.
        self._add(show_suggested_field='false')
        p = Product.all_objects.get(name='No-Sticker Bolt')
        self.assertFalse(p.show_suggested_price)

    def test_manual_prices_not_overwritten_on_add(self):
        # In MANUAL pricing mode the supplier's hand-typed per-pack price must
        # survive the save — auto-sync must not clobber it. (Regression: add
        # path used to run sync_variant_stocks() unconditionally.)
        self._add(
            name='Manual Priced Item',
            pricing_mode_field='manual',
            show_suggested_field='false',
            **{'variants-0-selling_price': '100.00', 'variants-0-mrp': ''},
        )
        p = Product.all_objects.get(name='Manual Priced Item')
        self.assertEqual(p.pricing_mode, 'manual')
        # 1kg auto price would be 200 (0.2/g × 1000); manual prices may be lower.
        self.assertEqual(p.variants.get().selling_price, Decimal('100.00'))

    def test_supplier_pack_rules_are_saved_from_variant_payload(self):
        self._add(
            name='Case Packed Loose Item',
            pricing_mode_field='manual',
            allow_loose_distribution='1',
            variants_json=json.dumps([{
                'name': '',
                'pool_grams': 5000,
                'packs': [{
                    'label': '5kg sack',
                    'unit_grams': 5000,
                    'selling_price': '750.00',
                    'stock': 10,
                    'supplier_can_supply': True,
                    'supplier_order_multiple': 4,
                    'supplier_supplies_loose': True,
                }],
            }]),
        )
        variant = Product.all_objects.get(name='Case Packed Loose Item').variants.get()

        self.assertEqual(variant.supplier_order_multiple, 4)
        self.assertTrue(variant.supplier_supplies_loose)

    def test_mrp_takes_precedence_over_suggested_price(self):
        # A printed MRP and a no-MRP suggestion are mutually exclusive modes.
        self._add(show_mrp_field='true', show_suggested_field='true',
                  **{'variants-0-mrp': '300.00', 'variants-0-suggested_price': '240.00'})
        p = Product.all_objects.get(name='No-Sticker Bolt')
        self.assertTrue(p.show_mrp)
        self.assertFalse(p.show_suggested_price)
        v = p.variants.get()
        self.assertEqual(v.mrp, Decimal('300.00'))
        self.assertIsNone(v.suggested_price)


class DirectLooseSupplyTests(TestCase):
    def setUp(self):
        self.world = build_world()
        self.product = self.world.shared_product
        self.product.direct_supply = True
        self.product.allow_loose_distribution = True
        self.product.save(update_fields=['direct_supply', 'allow_loose_distribution'])
        self.variant = self.product.variants.first()
        self.variant.supplier_supplies_loose = True
        self.variant.save(update_fields=['supplier_supplies_loose'])
        self.client_s = login_as(self.client, self.world.supplier_user)

    def test_supplier_delivers_whole_source_packs_and_deducts_stock(self):
        req = RestockRequest.objects.create(
            distributor=self.world.shop_a,
            variant=self.variant,
            quantity_requested=2,
            loose_grams=600,
        )

        self.client_s.post(reverse('suppliers:restock_requests'), {
            'req_id': req.pk,
            'action': 'deliver_direct',
        })

        req.refresh_from_db()
        self.variant.refresh_from_db()
        loose = DistributorLooseStock.objects.get(
            distributor=self.world.shop_a,
            product=self.product,
        )
        self.assertEqual(req.status, 'delivered')
        self.assertEqual(req.loose_grams, 1000)
        self.assertEqual(self.variant.stock, 98)
        self.assertEqual(loose.quantity_grams, 1000)


class SupplierDashboardFinancialSummaryTests(TestCase):
    def setUp(self):
        self.world = build_world()
        self.client_s = login_as(self.client, self.world.supplier_user)

    def _order(self, status):
        return Order.objects.create(
            customer=self.world.customer_a,
            distributor=self.world.shop_a,
            total_amount=1000,
            status=status,
            address='Test address',
            phone='0000000000',
        )

    def test_order_values_follow_item_delivery_and_exclude_cancelled_items(self):
        partial_order = self._order('partially_delivered')
        OrderItem.objects.create(
            order=partial_order, product=self.world.shared_product,
            variant=self.world.shared_variant, quantity=2, price=500,
            supplier_price=120, fulfillment_status='delivered',
        )
        OrderItem.objects.create(
            order=partial_order, product=self.world.shared_product,
            variant=self.world.shared_variant, quantity=3, price=500,
            supplier_price=130, fulfillment_status='pending',
        )
        OrderItem.objects.create(
            order=partial_order, product=self.world.shared_product,
            variant=self.world.shared_variant, quantity=9, price=500,
            supplier_price=100, fulfillment_status='cancelled',
        )
        cancelled_order = self._order('cancelled')
        OrderItem.objects.create(
            order=cancelled_order, product=self.world.shared_product,
            variant=self.world.shared_variant, quantity=5, price=500,
            supplier_price=100, fulfillment_status='pending',
        )
        partial_order.role_settlements.create(
            supplier=self.world.supplier, stockist=self.world.stockist,
            stage='stockist_to_supplier', amount_due=1530,
            amount_paid=120, status='confirmed',
        )
        partial_order.items.filter(supplier_price=120).update(
            fulfillment_status='delivered'
        )
        delivered_item = partial_order.items.get(supplier_price=120)
        delivered_item.supplier_shipment.status = 'received'
        delivered_item.supplier_shipment.save(update_fields=['status'])

        response = self.client_s.get(reverse('suppliers:dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['order_value'], Decimal('2030'))
        self.assertEqual(response.context['fund_received'], Decimal('120'))
        self.assertEqual(response.context['sent_awaiting_funds'], Decimal('120'))
        self.assertEqual(response.context['yet_to_send'], Decimal('390'))
        self.assertEqual(response.context['cancelled_order_value'], Decimal('1400'))
        self.assertEqual(
            response.context['order_value'],
            response.context['fund_received']
            + response.context['sent_awaiting_funds']
            + response.context['yet_to_send']
            + response.context['cancelled_order_value'],
        )

    def test_partial_confirmed_settlement_splits_sent_value_into_received_and_unpaid(self):
        pending_order = self._order('confirmed')
        item = OrderItem.objects.create(
            order=pending_order, product=self.world.shared_product,
            variant=self.world.shared_variant, quantity=10, price=500,
            supplier_price=100,
        )
        item.supplier_shipment.status = 'dispatched'
        item.supplier_shipment.save(update_fields=['status'])
        RoleSettlement.objects.create(
            order=pending_order, stage='stockist_to_supplier',
            stockist=self.world.stockist, supplier=self.world.supplier,
            amount_due=1000, amount_paid=250, status='confirmed',
        )

        response = self.client_s.get(reverse('suppliers:dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['fund_received'], Decimal('250'))
        self.assertEqual(response.context['sent_awaiting_funds'], Decimal('750'))
        self.assertEqual(response.context['yet_to_send'], Decimal('0'))

    def test_shipment_status_counts_orders_with_product_line_counts(self):
        awaiting_order = self._order('confirmed')
        ready_order = self._order('confirmed')
        for _ in range(2):
            OrderItem.objects.create(
                order=awaiting_order, product=self.world.shared_product,
                variant=self.world.shared_variant, quantity=4, price=500,
                supplier_price=100,
            )
        ready_item = OrderItem.objects.create(
            order=ready_order, product=self.world.shared_product,
            variant=self.world.shared_variant, quantity=3, price=500,
            supplier_price=100,
        )
        ready_item.supplier_shipment.status = 'confirmed'
        ready_item.supplier_shipment.save(update_fields=['status'])

        response = self.client_s.get(reverse('suppliers:dashboard'))

        counters = {item['key']: item for item in response.context['shipment_counters']}
        self.assertEqual(counters['awaiting']['order_count'], 1)
        self.assertEqual(counters['awaiting']['product_count'], 2)
        self.assertEqual(counters['confirmed']['order_count'], 1)
        self.assertEqual(counters['confirmed']['product_count'], 1)


class SupplierOrdersShipmentStatusFilterTests(TestCase):
    def setUp(self):
        self.world = build_world()
        self.client_s = login_as(self.client, self.world.supplier_user)

    def _order(self):
        return Order.objects.create(
            customer=self.world.customer_a,
            distributor=self.world.shop_a,
            total_amount=1000,
            status='confirmed',
            address='Test address',
            phone='0000000000',
        )

    def _item(self, order, shipment_status):
        item = OrderItem.objects.create(
            order=order,
            product=self.world.shared_product,
            variant=self.world.shared_variant,
            quantity=1,
            price=500,
            supplier_price=100,
        )
        item.supplier_shipment.status = shipment_status
        item.supplier_shipment.save(update_fields=['status'])
        return item

    def test_orders_tabs_count_orders_and_products_by_shipment_status(self):
        mixed_order = self._order()
        awaiting_items = [self._item(mixed_order, 'awaiting') for _ in range(2)]
        ready_item = self._item(mixed_order, 'confirmed')
        dispatched_item = self._item(self._order(), 'dispatched')

        response = self.client_s.get(reverse('suppliers:orders'))

        self.assertEqual(response.status_code, 200)
        counters = {
            tab['key']: tab for tab in response.context['shipment_status_tabs']
        }
        self.assertEqual(counters['awaiting']['order_count'], 1)
        self.assertEqual(counters['awaiting']['product_count'], 2)
        self.assertEqual(counters['confirmed']['order_count'], 1)
        self.assertEqual(counters['confirmed']['product_count'], 1)
        self.assertEqual(counters['dispatched']['order_count'], 1)
        self.assertEqual(counters['dispatched']['product_count'], 1)
        self.assertNotContains(response, 'Needs action')
        self.assertNotContains(response, '>Completed<')
        self.assertNotContains(response, '>All<')

        response = self.client_s.get(
            f'{reverse("suppliers:orders")}?tab=confirmed'
        )

        self.assertEqual(response.context['tab'], 'confirmed')
        self.assertEqual(len(response.context['order_groups']), 1)
        self.assertEqual(
            [line.pk for line, _shipment in response.context['order_groups'][0]['lines']],
            [ready_item.pk],
        )
        self.assertNotIn(awaiting_items[0].pk, [
            line.pk for line, _shipment in response.context['order_groups'][0]['lines']
        ])
        self.assertNotEqual(dispatched_item.order_id, mixed_order.pk)


class VariantSaveIntegrityTests(TestCase):
    """Adding/editing a product must never leave a product with no packs, and an
    edit must NEVER hard-delete variant rows (that cascades to every shop price,
    stock row, price-history entry and order line pointing at the pack)."""

    def setUp(self):
        self.world = build_world()
        self.cat = make_category(name='Hardware', slug='hardware', code='HW')
        self.client_s = login_as(self.client, self.world.supplier_user)

    def _post(self, name, variants, **over):
        """variants: list of dicts (selling_price, unit_grams, label, ...)."""
        data = {
            'name': name, 'category': str(self.cat.pk), 'description': '',
            'stock_amount': '100', 'stock_unit': 'kg', 'price_per_kg': '200',
            'pricing_mode_field': 'manual',
            'show_mrp_field': 'false', 'show_suggested_field': 'false',
            'variants-TOTAL_FORMS': str(len(variants)), 'variants-INITIAL_FORMS': '0',
            'variants-MIN_NUM_FORMS': '1', 'variants-MAX_NUM_FORMS': '10',
        }
        for i, v in enumerate(variants):
            data[f'variants-{i}-label'] = v.get('label', '1kg')
            data[f'variants-{i}-selling_price'] = str(v.get('selling_price', '200'))
            data[f'variants-{i}-mrp'] = str(v.get('mrp', '') or '')
            data[f'variants-{i}-suggested_price'] = str(v.get('suggested_price', '') or '')
            data[f'variants-{i}-stock'] = str(v.get('stock', 100))
            data[f'variants-{i}-unit_grams'] = str(v.get('unit_grams', 1000))
            data[f'variants-{i}-is_active'] = 'on'
            data[f'variants-{i}-id'] = ''
        data.update(over)
        return data

    def _product_with_variant(self, label='1kg', ug=1000, price='200'):
        p = Product.objects.create(
            name='Editable', supplier=self.world.supplier, category=self.cat,
            status='approved', master_unit='g', master_stock_grams=0,
            master_price_per_unit=Decimal('0'), pricing_mode='manual', charges_confirmed=True,
        )
        v = ProductVariant.objects.create(product=p, label=label, selling_price=Decimal(price),
                                          stock=100, unit_grams=ug)
        return p, v

    # ── add ──
    def test_add_rejects_empty_variant_set(self):
        data = self._post('Zombie A', [])
        data['variants-TOTAL_FORMS'] = '0'
        self.client_s.post(reverse('suppliers:product_add'), data)
        self.assertFalse(Product.all_objects.filter(name='Zombie A').exists())

    def test_add_rejects_invalid_price_no_zombie(self):
        data = self._post('Zombie B', [{'selling_price': '0'}])   # price 0 → invalid
        self.client_s.post(reverse('suppliers:product_add'), data)
        self.assertFalse(Product.all_objects.filter(name='Zombie B').exists())

    # ── edit: no data loss ──
    def test_edit_invalid_preserves_variants_and_dvp(self):
        p, v = self._product_with_variant()
        dvp = DistributorVariantPrice.objects.create(
            distributor=self.world.shop_a, variant=v,
            base_price=Decimal('200'), selling_price=Decimal('250'))
        # Invalid edit (price 0) must save NOTHING.
        self.client_s.post(reverse('suppliers:product_edit', args=[p.pk]),
                           self._post('Editable', [{'selling_price': '0', 'unit_grams': 1000}]))
        v.refresh_from_db()
        self.assertEqual(v.selling_price, Decimal('200'))          # unchanged
        self.assertTrue(DistributorVariantPrice.objects.filter(pk=dvp.pk).exists())

    def test_edit_valid_updates_in_place_preserving_dvp(self):
        p, v = self._product_with_variant()
        dvp = DistributorVariantPrice.objects.create(
            distributor=self.world.shop_a, variant=v,
            base_price=Decimal('200'), selling_price=Decimal('250'))
        self.client_s.post(reverse('suppliers:product_edit', args=[p.pk]),
                           self._post('Editable', [{'selling_price': '210', 'unit_grams': 1000}]))
        v.refresh_from_db()
        self.assertEqual(v.selling_price, Decimal('210'))          # updated in place
        # The pack row (pk) survived, so the shop's price row still points at it.
        self.assertTrue(DistributorVariantPrice.objects.filter(pk=dvp.pk, variant=v).exists())

    def test_edit_removed_pack_is_deactivated_not_deleted(self):
        p, v1 = self._product_with_variant()
        v2 = ProductVariant.objects.create(product=p, label='500g', selling_price=Decimal('120'),
                                           stock=50, unit_grams=500)
        # Submit only the 1kg pack — the 500g pack should be SOFT-removed.
        self.client_s.post(reverse('suppliers:product_edit', args=[p.pk]),
                           self._post('Editable', [{'selling_price': '200', 'unit_grams': 1000}]))
        v2.refresh_from_db()
        self.assertTrue(ProductVariant.objects.filter(pk=v2.pk).exists())   # row kept
        self.assertFalse(v2.is_active)                                      # just deactivated
