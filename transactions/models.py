from django.db import models
from django.conf import settings
from products.models import Product, ProductVariant
from accounts.models import User
from orders.models import Order, OrderItem


class ProductTransaction(models.Model):
    """Track all product lifecycle events"""
    EVENT_CHOICES = (
        ('created', 'Created'),
        ('activated', 'Activated'),
        ('deactivated', 'Deactivated'),
        ('suspended', 'Suspended'),
        ('deleted', 'Deleted'),
        ('restored', 'Restored'),
        ('status_changed', 'Status Changed'),
        ('price_updated', 'Price Updated'),
        ('stock_updated', 'Stock Updated'),
    )
    
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='transactions')
    event = models.CharField(max_length=20, choices=EVENT_CHOICES)
    old_value = models.JSONField(default=dict, blank=True)
    new_value = models.JSONField(default=dict, blank=True)
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, 
        null=True, related_name='product_transactions'
    )
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f'{self.product.name} - {self.event} ({self.created_at.strftime("%Y-%m-%d %H:%M")})'


class RoleTransaction(models.Model):
    """Track all user role lifecycle events"""
    EVENT_CHOICES = (
        ('role_added', 'Role Added'),
        ('role_removed', 'Role Removed'),
        ('status_changed', 'Status Changed'),
        ('activated', 'Activated'),
        ('suspended', 'Suspended'),
        ('deleted', 'Deleted'),
        ('restored', 'Restored'),
    )
    
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, 
        related_name='role_transactions'
    )
    event = models.CharField(max_length=20, choices=EVENT_CHOICES)
    role_affected = models.CharField(max_length=50, blank=True)
    old_value = models.CharField(max_length=100, blank=True)
    new_value = models.CharField(max_length=100, blank=True)
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, 
        null=True, related_name='initiated_role_transactions'
    )
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f'{self.user.username} - {self.event} ({self.created_at.strftime("%Y-%m-%d %H:%M")})'


class OrderTransaction(models.Model):
    """Track all order lifecycle events"""
    EVENT_CHOICES = (
        ('created', 'Created'),
        ('confirmed', 'Confirmed'),
        ('processing', 'Processing'),
        ('shipped', 'Shipped'),
        ('partially_delivered', 'Partially Delivered'),
        ('delivered', 'Delivered'),
        ('cancelled', 'Cancelled'),
        ('returned', 'Returned'),
        ('refunded', 'Refunded'),
        ('payment_received', 'Payment Received'),
        ('payment_failed', 'Payment Failed'),
    )
    
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='transactions')
    event = models.CharField(max_length=30, choices=EVENT_CHOICES)
    old_status = models.CharField(max_length=30, blank=True)
    new_status = models.CharField(max_length=30, blank=True)
    details = models.JSONField(default=dict, blank=True)
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, 
        null=True, related_name='order_transactions'
    )
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f'Order #{self.order.id} - {self.event} ({self.created_at.strftime("%Y-%m-%d %H:%M")})'


class OrderItemTransaction(models.Model):
    """Track individual order item lifecycle"""
    EVENT_CHOICES = (
        ('added', 'Added to Order'),
        ('quantity_updated', 'Quantity Updated'),
        ('fulfillment_pending', 'Fulfillment Pending'),
        ('shipped', 'Shipped'),
        ('delivered', 'Delivered'),
        ('backordered', 'Backordered'),
        ('cancelled', 'Cancelled'),
        ('returned', 'Returned'),
    )
    
    item = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name='transactions')
    event = models.CharField(max_length=30, choices=EVENT_CHOICES)
    old_value = models.CharField(max_length=100, blank=True)
    new_value = models.CharField(max_length=100, blank=True)
    details = models.JSONField(default=dict, blank=True)
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, 
        null=True, related_name='item_transactions'
    )
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f'Order #{self.item.order.id} Item {self.item.id} - {self.event}'


class VariantTransaction(models.Model):
    """Track variant-level lifecycle"""
    EVENT_CHOICES = (
        ('created', 'Created'),
        ('activated', 'Activated'),
        ('deactivated', 'Deactivated'),
        ('price_updated', 'Price Updated'),
        ('stock_updated', 'Stock Updated'),
    )
    
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name='transactions')
    event = models.CharField(max_length=20, choices=EVENT_CHOICES)
    old_value = models.CharField(max_length=100, blank=True)
    new_value = models.CharField(max_length=100, blank=True)
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, 
        null=True, related_name='variant_transactions'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f'{self.variant.product.name} ({self.variant.label}) - {self.event}'
