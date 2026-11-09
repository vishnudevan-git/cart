from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from accounts.models import User
from orders.models import Order

class DeliveryAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin_user = User.objects.create_superuser(username='admin', password='password', role='admin')
        self.customer = User.objects.create_user(username='customer', password='password', role='customer')
        
        self.order1 = Order.objects.create(
            customer=self.customer, total_amount=100, status='confirmed', address='123 Main', phone='000'
        )
        self.order2 = Order.objects.create(
            customer=self.customer, total_amount=200, status='pending', address='456 Elm', phone='111'
        )

    def test_login(self):
        response = self.client.post(reverse('delivery:api_login'), {'username': 'admin', 'password': 'password'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('token', response.data)

    def test_login_unauthorized(self):
        response = self.client.post(reverse('delivery:api_login'), {'username': 'customer', 'password': 'password'})
        self.assertEqual(response.status_code, 403)

    def test_list_deliveries(self):
        token_response = self.client.post(reverse('delivery:api_login'), {'username': 'admin', 'password': 'password'})
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token_response.data['token'])
        
        response = self.client.get(reverse('delivery:api_order_list'))
        self.assertEqual(response.status_code, 200)
        # Should only return 'confirmed' orders and not 'pending' as defined in view
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['status'], 'confirmed')

    def test_update_delivery_status(self):
        token_response = self.client.post(reverse('delivery:api_login'), {'username': 'admin', 'password': 'password'})
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token_response.data['token'])
        
        response = self.client.patch(reverse('delivery:api_order_update', args=[self.order1.id]), {'status': 'delivered'})
        self.assertEqual(response.status_code, 200)
        
        self.order1.refresh_from_db()
        self.assertEqual(self.order1.status, 'delivered')
        self.assertIsNotNone(self.order1.delivery.delivered_at)
