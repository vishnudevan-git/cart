from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from products.models import Product, Category
from suppliers.models import Supplier
from orders.models import Order
from transactions.models import ProductTransaction, RoleTransaction, OrderTransaction

User = get_user_model()


class TransactionTestCase(TestCase):
    def setUp(self):
        # Create admin user
        self.admin = User.objects.create_user(
            username='admin',
            password='admin@123',
            email='admin@test.com'
        )
        self.admin.add_role('admin')
        self.admin.save()
        
        # Create supplier
        self.supplier = User.objects.create_user(
            username='supplier',
            password='supplier@123',
            email='supplier@test.com'
        )
        self.supplier.add_role('supplier')
        self.supplier.save()
        
        # Create supplier profile
        self.supplier_profile = Supplier.objects.create(
            user=self.supplier,
            company_name='Test Supplier',
            category='organic',
            status='approved'
        )
        
        # Create category
        self.category = Category.objects.create(
            name='Test Category',
            slug='test-category',
            code='TC'
        )
        
        # Create product
        self.product = Product.objects.create(
            supplier=self.supplier_profile,
            category=self.category,
            name='Test Product',
            status='pending'
        )
        
        self.client = Client()
    
    def test_product_activation(self):
        """Test product activation transaction"""
        self.client.login(username='admin', password='admin@123')
        
        response = self.client.post(
            f'/transactions/products/{self.product.id}/activate/',
            {'reason': 'Test activation'}
        )
        
        # Check product status changed
        self.product.refresh_from_db()
        self.assertEqual(self.product.status, 'approved')
        
        # Check transaction created
        txn = ProductTransaction.objects.filter(
            product=self.product,
            event='activated'
        ).first()
        self.assertIsNotNone(txn)
        self.assertEqual(txn.initiated_by, self.admin)
    
    def test_product_deactivation(self):
        """Test product deactivation transaction"""
        self.client.login(username='admin', password='admin@123')
        
        response = self.client.post(
            f'/transactions/products/{self.product.id}/deactivate/',
            {'reason': 'Test deactivation'}
        )
        
        self.product.refresh_from_db()
        self.assertEqual(self.product.status, 'rejected')
        
        txn = ProductTransaction.objects.filter(
            product=self.product,
            event='deactivated'
        ).first()
        self.assertIsNotNone(txn)
    
    def test_role_addition(self):
        """Test adding role to user"""
        self.client.login(username='admin', password='admin@123')
        
        response = self.client.post(
            f'/transactions/users/{self.supplier.id}/add-role/',
            {'role': 'distributor', 'reason': 'Test role addition'}
        )
        
        self.supplier.refresh_from_db()
        self.assertIn('distributor', self.supplier.get_roles_list())
        
        txn = RoleTransaction.objects.filter(
            user=self.supplier,
            event='role_added'
        ).first()
        self.assertIsNotNone(txn)
    
    def test_user_suspension(self):
        """Test user suspension"""
        self.client.login(username='admin', password='admin@123')
        
        response = self.client.post(
            f'/transactions/users/{self.supplier.id}/suspend/',
            {'reason': 'Test suspension'}
        )
        
        self.supplier.refresh_from_db()
        self.assertFalse(self.supplier.is_active)
        
        txn = RoleTransaction.objects.filter(
            user=self.supplier,
            event='suspended'
        ).first()
        self.assertIsNotNone(txn)
    
    def test_dashboard_access(self):
        """Test dashboard access control"""
        self.client.login(username='supplier', password='supplier@123')
        
        response = self.client.get('/transactions/products/')
        self.assertEqual(response.status_code, 302)  # Redirect for non-admin
        
        self.client.login(username='admin', password='admin@123')
        response = self.client.get('/transactions/products/')
        self.assertEqual(response.status_code, 200)  # Admin can access
