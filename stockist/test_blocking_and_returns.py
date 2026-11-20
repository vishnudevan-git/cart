"""Blocked stock on supplier restock requests, and stock returned on customer cancel."""
from django.test import TestCase
from django.urls import reverse

from accounts.factories import build_world, login_as
from distributors.models import DistributorStock
from orders.models import Order, OrderItem
from products.models import ProductVariant
from stockist.models import StockistInventory, StockistRestockRequest


class SupplierBlockingTests(TestCase):
    """A stockist's request holds packs at once; the supplier still holds them until it ships."""

    def setUp(self):
        self.world = build_world()
        self.variant = self.world.shared_variant
        ProductVariant.objects.filter(pk=self.variant.pk).update(stock=10)
        self.client_st = login_as(self.client_class(), self.world.stockist_user)
        self.client_s = login_as(self.client_class(), self.world.supplier_user)

    def _request(self, qty):
        return self.client_st.post(reverse('stockist:restock_requests'), {
            'variant_id': self.variant.pk, 'quantity': str(qty)}, follow=True)

    def _variant(self):
        return ProductVariant.objects.get(pk=self.variant.pk)

    def test_request_blocks_packs_without_reducing_supplier_stock(self):
        self._request(4)
        v = self._variant()
        self.assertEqual(v.stock, 10)
        self.assertEqual(v.blocked_stock, 4)
        self.assertEqual(v.available_stock, 6)

    def test_second_stockist_cannot_request_blocked_packs(self):
        self._request(4)
        response = self._request(7)
        self.assertEqual(self._variant().blocked_stock, 4)
        self.assertEqual(StockistRestockRequest.objects.count(), 1)
        self.assertContains(response, 'Only 6')

    def test_rejecting_a_request_releases_the_block(self):
        self._request(4)
        req = StockistRestockRequest.objects.get()
        self.client_s.post(reverse('suppliers:restock_requests'),
                           {'kind': 'stockist', 'req_id': req.pk, 'action': 'reject'})
        self.assertEqual(self._variant().blocked_stock, 0)

    def test_shipping_a_request_lowers_stock_and_releases_the_block(self):
        self._request(4)
        req = StockistRestockRequest.objects.get()
        self.client_s.post(reverse('suppliers:restock_requests'),
                           {'kind': 'stockist', 'req_id': req.pk, 'action': 'fulfil'})
        v = self._variant()
        self.assertEqual(v.stock, 6)
        self.assertEqual(v.blocked_stock, 0)
        self.assertEqual(StockistInventory.objects.get(
            stockist=self.world.stockist, variant=v).quantity, 4)

    def test_pack_with_no_free_stock_is_hidden_from_stockist_list(self):
        ProductVariant.objects.filter(pk=self.variant.pk).update(blocked_stock=10)
        body = self.client_st.get(reverse('stockist:inventory')).content.decode()
        self.assertNotIn('Marketplace Cashew', body)

    def test_automatic_restock_respects_the_block(self):
        from stockist.auto_restock import check_stockist
        ProductVariant.objects.filter(pk=self.variant.pk).update(blocked_stock=8)
        StockistInventory.objects.create(
            stockist=self.world.stockist, variant=self.variant, quantity=0)
        self.world.stockist.auto_restock_enabled = True
        self.world.stockist.auto_restock_threshold = 5
        self.world.stockist.auto_restock_quantity = 5
        self.world.stockist.save()
        check_stockist(self.world.stockist)
        self.assertFalse(StockistRestockRequest.objects.exists())
        self.assertEqual(self._variant().blocked_stock, 8)


class CustomerReturnTests(TestCase):
    """A customer's cancel or reduce returns packs to whoever gave them to the customer."""

    def setUp(self):
        self.world = build_world()
        self.variant = self.world.shared_variant
        self.client_c = login_as(self.client, self.world.customer_a)
        self.order = Order.objects.create(
            customer=self.world.customer_a, distributor=self.world.shop_a,
            subtotal=300, total_amount=300, status='pending',
            address='1 Street', phone='9100000001', fulfillment_source='supplier',
        )

    def _item(self, source, qty=3):
        return OrderItem.objects.create(
            order=self.order, product=self.variant.product, variant=self.variant,
            variant_label=self.variant.label, quantity=qty, price=100, base_price=100,
            supplier_price=100, mrp=100, stock_source=source,
        )

    def _edit(self, **data):
        return self.client_c.post(reverse('orders:order_edit', args=[self.order.pk]), data)

    def test_removing_a_supplier_item_returns_packs_to_supplier(self):
        ProductVariant.objects.filter(pk=self.variant.pk).update(stock=7)   # 3 already taken
        item = self._item('supplier', 3)
        self._edit(action='remove_item', item_id=item.pk)
        self.assertEqual(ProductVariant.objects.get(pk=self.variant.pk).stock, 10)

    def test_reducing_a_supplier_item_returns_only_the_difference(self):
        ProductVariant.objects.filter(pk=self.variant.pk).update(stock=7)
        item = self._item('supplier', 3)
        self._edit(action='update_qty', item_id=item.pk, quantity=1)
        self.assertEqual(ProductVariant.objects.get(pk=self.variant.pk).stock, 9)

    def test_removing_a_shop_item_returns_packs_to_the_shop(self):
        item = self._item('shop', 3)
        self._edit(action='remove_item', item_id=item.pk)
        stock = DistributorStock.objects.get(distributor=self.world.shop_a, variant=self.variant)
        self.assertEqual(stock.quantity, 3)
        self.assertEqual(ProductVariant.objects.get(pk=self.variant.pk).stock, 100)

    def test_distributor_cannot_cancel_an_order(self):
        client_d = login_as(self.client_class(), self.world.shop_a_user)
        client_d.post(reverse('distributors:order_detail', args=[self.order.pk]),
                      {'status': 'cancelled'})
        self.order.refresh_from_db()
        self.assertNotEqual(self.order.status, 'cancelled')


class WholesalerBlockingTests(TestCase):
    """A distributor's request to its stockist holds the stockist's packs at once."""

    def setUp(self):
        self.world = build_world()
        self.world.wholesaler.stockist = self.world.stockist
        self.world.wholesaler.save(update_fields=['stockist'])
        self.variant = self.world.shared_variant
        StockistInventory.objects.create(
            stockist=self.world.stockist, variant=self.variant, quantity=10)
        self.client_w = login_as(self.client_class(), self.world.wholesaler_user)
        self.client_st = login_as(self.client_class(), self.world.stockist_user)

    def _inv(self):
        return StockistInventory.objects.get(stockist=self.world.stockist, variant=self.variant)

    def _request(self, qty):
        self.client_w.post(reverse('wholesaler:my_stock'),
                           {'action': 'restock', f'qty_{self.variant.pk}': str(qty)})

    def test_request_blocks_stockist_packs(self):
        from distributors.wholesaler_models import WholesalerRestockRequest
        self._request(4)
        self.assertEqual(WholesalerRestockRequest.objects.count(), 1)
        self.assertEqual(self._inv().blocked_quantity, 4)
        self.assertEqual(self._inv().quantity, 10)

    def test_second_distributor_request_is_refused_when_free_stock_is_short(self):
        from distributors.wholesaler_models import WholesalerRestockRequest
        self._request(4)
        self._request(7)
        self.assertEqual(WholesalerRestockRequest.objects.count(), 1)
        self.assertEqual(self._inv().blocked_quantity, 4)

    def test_rejecting_releases_the_block(self):
        from distributors.wholesaler_models import WholesalerRestockRequest
        self._request(4)
        req = WholesalerRestockRequest.objects.get()
        self.client_st.post(reverse('stockist:distributor_restock'),
                            {'kind': 'wholesaler', 'req_id': req.pk, 'action': 'reject'})
        self.assertEqual(self._inv().blocked_quantity, 0)
        self.assertEqual(self._inv().quantity, 10)

    def test_delivery_takes_packs_out_and_releases_the_block(self):
        from distributors.wholesaler_models import WholesalerRestockRequest
        self._request(4)
        req = WholesalerRestockRequest.objects.get()
        self.client_st.post(reverse('stockist:distributor_restock'),
                            {'kind': 'wholesaler', 'req_id': req.pk, 'action': 'deliver'})
        self.assertEqual(self._inv().quantity, 6)
        self.assertEqual(self._inv().blocked_quantity, 0)

class NoBufferRestockTests(TestCase):
    """No-buffer ON: the distributor keeps no stock, so it has no restock option."""

    def setUp(self):
        self.world = build_world()
        self.world.wholesaler.stockist = self.world.stockist
        self.world.wholesaler.save(update_fields=['stockist'])
        self.variant = self.world.shared_variant
        self.client_w = login_as(self.client_class(), self.world.wholesaler_user)
        self.url = reverse('wholesaler:my_stock')

    def test_restock_form_is_shown_when_no_buffer_is_off(self):
        body = self.client_w.get(self.url).content.decode()
        self.assertIn('Send Restock Request', body)

    def test_restock_form_is_hidden_when_no_buffer_is_on(self):
        from distributors.wholesaler_models import Wholesaler
        Wholesaler.objects.filter(pk=self.world.wholesaler.pk).update(no_buffer_stock=True)
        body = self.client_w.get(self.url).content.decode()
        self.assertNotIn('Send Restock Request', body)

    def test_restock_action_is_refused_when_no_buffer_is_on(self):
        from distributors.wholesaler_models import Wholesaler, WholesalerRestockRequest
        Wholesaler.objects.filter(pk=self.world.wholesaler.pk).update(no_buffer_stock=True)
        self.client_w.post(self.url, {'action': 'restock', f'qty_{self.variant.pk}': '4'})
        self.assertFalse(WholesalerRestockRequest.objects.exists())


class CustomerQuantityIncreaseTests(TestCase):
    """A placed order cannot gain packs: the stock for them was never taken."""

    def setUp(self):
        self.world = build_world()
        self.variant = self.world.shared_variant
        ProductVariant.objects.filter(pk=self.variant.pk).update(stock=7)
        self.client_c = login_as(self.client_class(), self.world.customer_a)
        self.order = Order.objects.create(
            customer=self.world.customer_a, distributor=self.world.shop_a,
            subtotal=300, total_amount=300, status='pending',
            address='1 Street', phone='9100000001', fulfillment_source='supplier',
        )
        self.item = OrderItem.objects.create(
            order=self.order, product=self.variant.product, variant=self.variant,
            variant_label=self.variant.label, quantity=3, price=100, base_price=100,
            supplier_price=100, mrp=100, stock_source='supplier',
        )

    def test_increase_is_refused_and_stock_is_untouched(self):
        response = self.client_c.post(
            reverse('orders:order_edit', args=[self.order.pk]),
            {'action': 'update_qty', 'item_id': self.item.pk, 'quantity': 5}, follow=True)
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 3)
        self.assertEqual(ProductVariant.objects.get(pk=self.variant.pk).stock, 7)
        self.assertContains(response, 'Place a new order')

    def test_reduce_still_works_and_returns_packs(self):
        self.client_c.post(
            reverse('orders:order_edit', args=[self.order.pk]),
            {'action': 'update_qty', 'item_id': self.item.pk, 'quantity': 1})
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 1)
        self.assertEqual(ProductVariant.objects.get(pk=self.variant.pk).stock, 9)