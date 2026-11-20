"""A supplier cannot create or rename a product to a live product's exact name."""
import json

from django.test import TestCase
from django.urls import reverse

from accounts.factories import build_world, login_as, make_category, make_shared_product, make_supplier
from products.forms import ProductForm
from products.models import Product


def _add_payload(cat_pk, name):
    return {
        'name': name,
        'category': str(cat_pk),
        'description': '',
        'stock_amount': '10', 'stock_unit': 'kg', 'price_per_kg': '200',
        'pricing_mode_field': 'manual',
        'show_mrp_field': 'false', 'show_suggested_field': 'false',
        'allow_bundle': '0',
        'variants_json': json.dumps([{'name': '', 'packs': [{
            'label': '500g', 'unit_grams': 500, 'selling_price': '100.00',
            'stock': 10, 'stock_entered': True, 'supplier_can_supply': True,
        }]}]),
    }


class ProductNameUniquenessTests(TestCase):
    def setUp(self):
        self.world = build_world()
        self.cat = make_category(name='Grains', slug='grains', code='GR')
        self.client_s = login_as(self.client, self.world.supplier_user)

    def test_add_with_live_name_of_same_supplier_is_refused(self):
        response = self.client_s.post(reverse('suppliers:product_add'),
                                      _add_payload(self.cat.pk, 'Marketplace Cashew'))
        self.assertEqual(Product.all_objects.filter(name='Marketplace Cashew').count(), 1)
        self.assertContains(response, 'You already have a product named')

    def test_add_with_new_name_still_works(self):
        self.client_s.post(reverse('suppliers:product_add'),
                           _add_payload(self.cat.pk, 'Brand New Rice'), follow=True)
        self.assertTrue(Product.all_objects.filter(name='Brand New Rice').exists())

    def test_name_of_deleted_product_can_be_reused(self):
        Product.all_objects.filter(pk=self.world.shared_product.pk).update(is_deleted=True)
        self.client_s.post(reverse('suppliers:product_add'),
                           _add_payload(self.cat.pk, 'Marketplace Cashew'), follow=True)
        self.assertEqual(Product.objects.filter(name='Marketplace Cashew').count(), 1)

    def test_other_suppliers_can_use_the_same_name(self):
        _, other = make_supplier(username='other@test.com', company='Other Farms')
        make_shared_product(other, self.cat, name='Shared Name', stock=5)
        form = ProductForm({'name': 'Shared Name', 'category': self.cat.pk,
                            'description': '', 'stock_unit': 'kg'},
                           supplier=self.world.supplier)
        self.assertTrue(form.is_valid(), form.errors)

    def test_renaming_to_a_live_name_is_refused(self):
        other_product = make_shared_product(self.world.supplier, self.cat,
                                            name='Other Item', stock=5)
        form = ProductForm({'name': 'Marketplace Cashew', 'category': self.cat.pk,
                            'description': '', 'stock_unit': 'kg'},
                           instance=other_product, supplier=self.world.supplier)
        self.assertFalse(form.is_valid())
        self.assertIn('name', form.errors)

    def test_keeping_own_name_on_edit_is_allowed(self):
        form = ProductForm({'name': 'Marketplace Cashew', 'category': self.cat.pk,
                            'description': '', 'stock_unit': 'kg'},
                           instance=self.world.shared_product, supplier=self.world.supplier)
        self.assertTrue(form.is_valid(), form.errors)

    def test_name_match_ignores_nothing_else_so_different_case_is_allowed(self):
        form = ProductForm({'name': 'marketplace cashew', 'category': self.cat.pk,
                            'description': '', 'stock_unit': 'kg'},
                           supplier=self.world.supplier)
        self.assertTrue(form.is_valid(), form.errors)
