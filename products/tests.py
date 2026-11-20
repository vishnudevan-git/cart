from django.test import TestCase
from django.urls import reverse

from accounts.factories import build_world
from products.models import ProductVariant


class BulkSourceVisibilityTests(TestCase):
    def setUp(self):
        self.world = build_world()
        self.source = self.world.shared_variant
        self.source.supplier_supplies_loose = True
        self.source.save(update_fields=['supplier_supplies_loose'])
        self.retail_pack = ProductVariant.objects.create(
            product=self.world.shared_product,
            label='1kg',
            selling_price='600.00',
            stock=20,
            unit_grams=1000,
        )
        session = self.client.session
        session['distributor_id'] = self.world.shop_a.pk
        session.save()

    def test_bulk_source_is_not_offered_as_a_customer_pack(self):
        response = self.client.get(
            reverse('products:product_detail', args=[self.world.shared_product.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [pack['id'] for pack in response.context['variants_data']],
            [self.retail_pack.pk],
        )

    def test_bulk_source_cannot_be_added_to_customer_cart(self):
        response = self.client.post(
            reverse('cart:add', args=[self.source.pk]),
            {'quantity': '1'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['success'])
        self.assertIn('not sold directly', response.json()['error'])

# Create your tests here.
