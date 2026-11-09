from django.urls import path
from . import views

app_name = 'transactions'

urlpatterns = [
    # Product Lifecycle
    path('products/', views.product_lifecycle_dashboard, name='product_dashboard'),
    path('products/<int:product_id>/', views.product_detail, name='product_detail'),
    path('products/<int:product_id>/activate/', views.activate_product, name='activate_product'),
    path('products/<int:product_id>/deactivate/', views.deactivate_product, name='deactivate_product'),
    path('products/<int:product_id>/delete/', views.delete_product, name='delete_product'),
    path('products/<int:product_id>/restore/', views.restore_product, name='restore_product'),
    
    # Role Management
    path('roles/', views.role_management_dashboard, name='role_management'),
    path('users/<int:user_id>/', views.user_detail, name='user_detail'),
    path('users/<int:user_id>/add-role/', views.add_role, name='add_role'),
    path('users/<int:user_id>/remove-role/', views.remove_role, name='remove_role'),
    path('users/<int:user_id>/suspend/', views.suspend_user, name='suspend_user'),
    path('users/<int:user_id>/activate/', views.activate_user, name='activate_user'),
    path('users/<int:user_id>/delete/', views.delete_user, name='delete_user'),
    
    # Order Lifecycle
    path('orders/', views.order_lifecycle_dashboard, name='order_dashboard'),
    path('orders/<int:order_id>/', views.order_detail, name='order_detail'),
    path('orders/<int:order_id>/confirm/', views.confirm_order, name='confirm_order'),
    path('orders/<int:order_id>/update-status/', views.update_order_status, name='update_order_status'),
    path('orders/<int:order_id>/cancel/', views.cancel_order, name='cancel_order'),
]
