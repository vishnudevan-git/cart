from django.test import TestCase
from products.models import Product, Category, ProductVariant
from accounts.models import User
from suppliers.models import Supplier

class AdvancedSearchTests(TestCase):
    def setUp(self):
        self.supplier_user = User.objects.create_user(username='supplier', password='password', role='supplier')
        self.supplier = Supplier.objects.create(user=self.supplier_user, company_name='Organic Farms', status='approved')
        
        self.category = Category.objects.create(name='Dry Fruits', slug='dry-fruits')
        self.product = Product.objects.create(
            supplier=self.supplier,
            category=self.category,
            name='Premium California Almonds',
            description='Very healthy nuts',
            status='approved'
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            label='500g pack',
            mrp=500,
            selling_price=450,
            stock=10
        )
    
    def test_search_by_name(self):
        from django.db.models import Q
        qs = Product.objects.filter(Q(name__icontains='Almond')).distinct()
        self.assertEqual(qs.count(), 1)
        
    def test_search_by_category(self):
        from django.db.models import Q
        qs = Product.objects.filter(Q(category__name__icontains='Dry Fruit')).distinct()
        self.assertEqual(qs.count(), 1)

    def test_search_by_variant(self):
        from django.db.models import Q
        qs = Product.objects.filter(Q(variants__label__icontains='500g')).distinct()
        self.assertEqual(qs.count(), 1)

    def test_search_by_supplier(self):
        from django.db.models import Q
        qs = Product.objects.filter(Q(supplier__company_name__icontains='Organic')).distinct()
        self.assertEqual(qs.count(), 1)
