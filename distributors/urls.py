from django.urls import path
from . import views
from . import own_product_views

app_name = 'distributors'

urlpatterns = [
    path('dashboard/', views.dashboard, name='dashboard'),
    # Marketplace own products — private to this one shop.
    path('my-products/', own_product_views.own_product_list, name='own_product_list'),
    path('my-products/new/', own_product_views.own_product_form, name='own_product_create'),
    path('my-products/template-search/', own_product_views.template_search, name='template_search'),
    path('my-products/library/', own_product_views.template_library, name='template_library'),
    path('my-products/<int:pk>/edit/', own_product_views.own_product_form, name='own_product_edit'),
    path('my-products/<int:pk>/toggle/', own_product_views.own_product_toggle, name='own_product_toggle'),
    path('my-products/<int:pk>/delete/', own_product_views.own_product_delete, name='own_product_delete'),
    path('products/', views.product_catalogue, name='product_catalogue'),
    path('products/toggle-auto-list/', views.toggle_auto_list, name='toggle_auto_list'),
    path('products/toggle-hide-out-of-stock/', views.toggle_hide_out_of_stock, name='toggle_hide_out_of_stock'),
    path('products/bulk-list-all/', views.bulk_list_all, name='bulk_list_all'),
    path('products/bulk-apply-global-pricing/', views.bulk_apply_global_pricing, name='bulk_apply_global_pricing'),
    path('products/bulk-toggle-listing/', views.bulk_toggle_listing, name='bulk_toggle_listing'),
    path('products/export/', views.catalogue_export, name='catalogue_export'),
    path('products/import/', views.catalogue_import, name='catalogue_import'),
    path('products/apply-global-pricing/', views.apply_global_pricing, name='apply_global_pricing'),
    path('products/<int:product_pk>/save/', views.save_product_listing, name='save_product_listing'),
    path('orders/', views.order_list, name='order_list'),
    path('orders/<int:pk>/', views.order_detail, name='order_detail'),
    path('customers/', views.customer_list, name='customer_list'),
    path('earnings/', views.earnings, name='earnings'),
    path('settings/', views.settings_view, name='settings'),
    path('price-history/', views.price_history, name='price_history'),
    path('reset-requests/', views.reset_requests, name='reset_requests'),
    path('delivery-slots/', views.delivery_slots, name='delivery_slots'),
    path('earnings-summary/', views.earnings_summary, name='earnings_summary'),
    path('claim-customer/', views.claim_customer, name='claim_customer'),
    path('my-stock/', views.my_stock, name='my_stock'),
    path('restock-requests/', views.restock_requests, name='restock_requests'),
    path('rewards/', views.reward_settings, name='reward_settings'),
    path('rewards/adjust/', views.reward_adjust, name='reward_adjust'),
    path('orders/<int:order_pk>/allocate-reward/', views.allocate_reward, name='allocate_reward'),
    path('orders/<int:order_pk>/fulfill-backorder/', views.fulfill_backorder, name='fulfill_backorder'),
    path('customers/<int:customer_pk>/reset-password/', views.reset_customer_password, name='reset_customer_password'),
    path('customers/<int:customer_pk>/delete/', views.delete_customer, name='delete_customer'),
    path('customers/<int:customer_pk>/', views.customer_detail, name='customer_detail'),
    path('storefront/', views.storefront, name='storefront'),
    path('delivery-zones/', views.delivery_zones, name='delivery_zones'),
]
