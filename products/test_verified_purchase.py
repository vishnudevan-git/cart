from django.test import TestCase
from accounts.models import User
from products.models import Product, Category, ProductVariant, ProductReview
from orders.models import Order, OrderItem

class VerifiedPurchaseTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testcustomer', password='password', role='customer')
        self.supplier = User.objects.create_user(username='testsupplier', password='password', role='supplier')
        from suppliers.models import Supplier
        self.supplier_profile = Supplier.objects.create(user=self.supplier, company_name='Test Co', status='approved')
        
        self.category = Category.objects.create(name='Test Category', slug='test-category')
        self.product = Product.objects.create(
            supplier=self.supplier_profile,
            category=self.category,
            name='Test Product',
            status='approved'
        )

    def test_review_without_purchase(self):
        review = ProductReview.objects.create(
            product=self.product,
            user=self.user,
            rating=5,
            comment='Good'
        )
        self.assertFalse(review.is_verified_purchase, "Should be False when user hasn't bought it")

    def test_review_with_delivered_purchase(self):
        order = Order.objects.create(
            customer=self.user,
            total_amount=90,
            status='delivered',
            address='123 Main',
            phone='1234567890'
        )
        OrderItem.objects.create(
            order=order,
            product=self.product,
            quantity=1,
            price=90,
            base_price=80,
            mrp=100
        )
        
        review = ProductReview.objects.create(
            product=self.product,
            user=self.user,
            rating=5,
            comment='Excellent!'
        )
        self.assertTrue(review.is_verified_purchase, "Should be True because user has a delivered order for it")
