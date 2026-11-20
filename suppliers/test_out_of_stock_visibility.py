"""Out-of-stock packs: hidden from the stockist's supplier list, and the Out of Stock
filter button only appears when there is something to filter."""
from django.test import TestCase
from django.urls import reverse

from accounts.factories import build_world, login_as, make_category, make_shared_product
from stockist.models import StockistInventory


class StockistSupplyListTests(TestCase):
    def setUp(self):
        self.world = build_world()
        spices = make_category(name='Spices', slug='spices', code='SP')
        self.pepper = make_shared_product(self.world.supplier, spices, name='Black Pepper', stock=0)
        self.client_st = login_as(self.client, self.world.stockist_user)
        self.url = reverse('stockist:inventory')

    def test_pack_with_zero_supplier_stock_is_hidden(self):
        body = self.client_st.get(self.url).content.decode()
        self.assertIn('Marketplace Cashew', body)
        self.assertNotIn('Black Pepper', body)

    def test_zero_stock_pack_is_hidden_even_if_stockist_holds_some(self):
        StockistInventory.objects.create(
            stockist=self.world.stockist, variant=self.pepper.variants.first(), quantity=5)
        body = self.client_st.get(self.url).content.decode()
        self.assertNotIn('Black Pepper', body)
        self.assertTrue(StockistInventory.objects.filter(
            stockist=self.world.stockist, variant=self.pepper.variants.first(), quantity=5).exists())

    def test_pack_reappears_when_supplier_stock_arrives(self):
        self.pepper.variants.update(stock=20)
        body = self.client_st.get(self.url).content.decode()
        self.assertIn('Black Pepper', body)


class OutOfStockButtonTests(TestCase):
    def setUp(self):
        self.world = build_world()
        self.client_s = login_as(self.client, self.world.supplier_user)
        self.url = reverse('suppliers:stock_manage')

    def test_button_hidden_when_nothing_is_out_of_stock(self):
        body = self.client_s.get(self.url).content.decode()
        self.assertNotIn('data-filter="out"', body)

    def test_button_shown_when_something_is_out_of_stock(self):
        spices = make_category(name='Spices', slug='spices', code='SP')
        make_shared_product(self.world.supplier, spices, name='Black Pepper', stock=0)
        body = self.client_s.get(self.url).content.decode()
        self.assertIn('data-filter="out"', body)
