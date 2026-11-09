from django.urls import path
from . import views

app_name = 'suppliers'

urlpatterns = [
    path('dashboard/', views.dashboard, name='dashboard'),
    path('update-address/', views.update_address, name='update_address'),
    path('products/', views.product_list, name='product_list'),
    path('products/add/', views.product_add, name='product_add'),
    path('products/<int:pk>/edit/', views.product_edit, name='product_edit'),
    path('products/<int:pk>/delete/', views.product_delete, name='product_delete'),
    path('products/bulk-upload/', views.bulk_upload, name='bulk_upload'),
    path('products/bulk-upload/template/', views.download_bulk_template, name='download_bulk_template'),
    path('stock/', views.stock_manage, name='stock_manage'),
    path('orders/', views.orders, name='orders'),
    path('price-history/', views.price_history, name='price_history'),
    path('bulk-images/', views.bulk_image_upload, name='bulk_image_upload'),
    path('payout-history/', views.payout_history, name='payout_history'),
    path('products/<int:pk>/toggle-pricing/', views.toggle_pricing_mode, name='toggle_pricing_mode'),
    path('storefront/', views.storefront, name='storefront'),
    path('restock-requests/', views.restock_requests, name='restock_requests'),
    path('procurement/', views.procurement_accounts, name='procurement_accounts'),
]
