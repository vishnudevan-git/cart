from django.urls import path
from . import views
from support import views as support_views

app_name = 'admin_panel'

urlpatterns = [
    path('dashboard/', views.dashboard, name='dashboard'),
    path('price-validator/', views.price_track_validator, name='price_track_validator'),
    path('price-trace/', views.price_trace, name='price_trace'),
    # Partner hierarchy (read-only org tree)
    path('hierarchy/', views.hierarchy, name='hierarchy'),
    # Suppliers
    path('suppliers/', views.supplier_list, name='supplier_list'),
    path('suppliers/create/', views.supplier_create, name='supplier_create'),
    path('suppliers/<int:pk>/', views.supplier_detail, name='supplier_detail'),
    path('suppliers/<int:pk>/suspend/', views.supplier_suspend, name='supplier_suspend'),
    path('suppliers/<int:pk>/activate/', views.supplier_activate, name='supplier_activate'),
    # Commission rules
    path('commission/', views.commission_rules, name='commission_rules'),
    path('commission/add/', views.commission_rule_add, name='commission_rule_add'),
    path('commission/<int:pk>/delete/', views.commission_rule_delete, name='commission_rule_delete'),
    path('commission/<int:pk>/toggle/', views.commission_rule_toggle, name='commission_rule_toggle'),
    # Stock
    path('stock/', views.stock_adjust, name='stock_adjust'),
    # Products
    path('products/', views.product_list, name='product_list'),
    path('products/<int:pk>/approve/', views.product_approve, name='product_approve'),
    path('products/<int:pk>/reject/', views.product_reject, name='product_reject'),
    path('products/<int:pk>/delete/', views.product_delete, name='product_delete'),
    path('products/<int:pk>/restore/', views.product_restore, name='product_restore'),
    path('marketplace/', views.marketplace, name='marketplace'),
    # Orders (read-only for admin)
    path('orders/', views.order_list, name='order_list'),
    path('orders/<int:pk>/', views.order_detail, name='order_detail'),
    path('orders/<int:pk>/update-status/', views.update_order_status, name='update_order_status'),
    # Categories
    path('categories/', views.category_list, name='category_list'),
    path('categories/add/', views.category_add, name='category_add'),
    path('categories/<int:pk>/delete/', views.category_delete, name='category_delete'),
    # Reports
    path('reports/', views.reports, name='reports'),
    path('reports/export/', views.export_reports, name='export_reports'),
    # Price history
    path('price-history/', views.price_history, name='price_history'),
    # Distributors
    path('distributors/', views.distributor_list, name='distributor_list'),
    path('distributors/create/', views.distributor_create, name='distributor_create'),
    path('distributors/<int:pk>/', views.distributor_detail, name='distributor_detail'),
    path('distributors/<int:pk>/toggle/', views.distributor_toggle, name='distributor_toggle'),
    path('distributors/<int:pk>/delete/', views.distributor_delete, name='distributor_delete'),
    path('distributors/<int:pk>/prices/', views.distributor_set_prices, name='distributor_set_prices'),
    # User Management
    path('users/', views.user_list, name='user_list'),
    path('users/create/', views.user_create, name='user_create'),
    path('customers/', views.customer_list, name='customer_list'),
    path('users/<int:pk>/', views.user_detail, name='user_detail'),
    path('users/<int:pk>/edit/', views.user_edit, name='user_edit'),
    path('users/<int:pk>/reset-password/', views.user_reset_password, name='user_reset_password'),
    path('users/<int:pk>/toggle-active/', views.user_toggle_active, name='user_toggle_active'),
    path('users/<int:pk>/delete/', views.user_delete, name='user_delete'),
    path('users/<int:pk>/restore/', views.user_restore, name='user_restore'),
    path('users/<int:pk>/purge/', views.user_purge, name='user_purge'),
    path('users/bulk/', views.bulk_user_action, name='bulk_user_action'),
    path('users/<int:pk>/impersonate/', views.impersonate, name='impersonate'),
    path('impersonate/return/', views.impersonate_return, name='impersonate_return'),
    # Password reset requests
    path('reset-requests/', views.reset_requests, name='reset_requests'),
    path('reset-requests/<int:pk>/action/', views.reset_request_action, name='reset_request_action'),
    # Export
    path('export/orders/', views.export_orders, name='export_orders'),
    # Low stock
    path('low-stock/', views.low_stock, name='low_stock'),
    # Bulk actions
    path('products/bulk/', views.bulk_product_action, name='bulk_product_action'),
    path('suppliers/bulk/', views.bulk_supplier_action, name='bulk_supplier_action'),
    path('distributors/bulk/', views.bulk_distributor_action, name='bulk_distributor_action'),
    path('wholesale-distributors/bulk/', views.bulk_wholesaler_action, name='bulk_wholesaler_action'),
    # Account lifecycle (soft-delete / restore / permanent archive) for partner roles
    path('partner/<str:role>/<int:pk>/delete/', views.partner_soft_delete, name='partner_soft_delete'),
    path('partner/<str:role>/<int:pk>/restore/', views.partner_restore, name='partner_restore'),
    path('partner/<str:role>/<int:pk>/purge/', views.partner_purge, name='partner_purge'),
    # Wholesale Distributors (the new upstream tier)
    path('wholesale-distributors/', views.wholesaler_list, name='wholesaler_list'),
    path('wholesale-distributors/create/', views.wholesaler_create, name='wholesaler_create'),
    path('wholesale-distributors/<int:pk>/', views.wholesaler_detail, name='wholesaler_detail'),
    path('wholesale-distributors/<int:pk>/toggle/', views.wholesaler_toggle, name='wholesaler_toggle'),
    path('wholesale-distributors/<int:pk>/assign/', views.wholesaler_assign, name='wholesaler_assign'),
    path('stockist/bulk/', views.bulk_stockist_action, name='bulk_stockist_action'),
    # Stockist
    path('stockist/', views.stockist_list, name='stockist_list'),
    path('stockist/create/', views.stockist_create, name='stockist_create'),
    path('stockist/<int:pk>/toggle/', views.stockist_toggle, name='stockist_toggle'),
    path('stockist/restock/', views.stockist_restock_list, name='stockist_restock'),
    # Support tickets
    path('support/', support_views.admin_ticket_list, name='support_tickets'),
    path('support/<int:pk>/respond/', support_views.admin_ticket_respond, name='support_respond'),
]
