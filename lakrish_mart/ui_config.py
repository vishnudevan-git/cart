"""Centralized UI defaults and reusable theme options."""

DEFAULT_BRAND_NAME = 'LaKrish Mart'
DEFAULT_THEME_COLOR = '#2e7d32'
DEFAULT_FONT_FAMILY = 'Inter, sans-serif'

# Reusable distributor/customer storefront color presets.
THEME_PRESET_COLORS = (
    {'name': 'Green', 'hex': '#2e7d32'},
    {'name': 'Blue', 'hex': '#1565c0'},
    {'name': 'Red', 'hex': '#c62828'},
    {'name': 'Purple', 'hex': '#6a1b9a'},
    {'name': 'Orange', 'hex': '#e65100'},
    {'name': 'Teal', 'hex': '#00838f'},
    {'name': 'Dark', 'hex': '#212121'},
)

SUPPLIER_SIDEBAR_ITEMS = (
    {'key': 'dashboard', 'label': 'Dashboard', 'icon': 'bi-speedometer2', 'url_name': 'suppliers:dashboard'},
    {'key': 'product_list', 'label': 'My Products', 'icon': 'bi-box-seam', 'url_name': 'suppliers:product_list'},
    {'key': 'product_add', 'label': 'Add Product', 'icon': 'bi-plus-circle', 'url_name': 'suppliers:product_add'},
    {'key': 'stock_manage', 'label': 'Manage Stock', 'icon': 'bi-layers', 'url_name': 'suppliers:stock_manage'},
    {'key': 'orders', 'label': 'Orders', 'icon': 'bi-receipt', 'url_name': 'suppliers:orders'},
    {'key': 'restock_requests', 'label': 'Restock Requests', 'icon': 'bi-arrow-repeat', 'url_name': 'suppliers:restock_requests'},
    {'key': 'price_history', 'label': 'Price History', 'icon': 'bi-graph-up', 'url_name': 'suppliers:price_history'},
    {'key': 'payout_history', 'label': 'Payout History', 'icon': 'bi-wallet2', 'url_name': 'suppliers:payout_history'},
    {'key': 'payments', 'label': 'Payments Center', 'icon': 'bi-cash-stack', 'url_name': 'payments:center'},
    {'key': 'procurement', 'label': 'Procurement Receivables', 'icon': 'bi-receipt', 'url_name': 'suppliers:procurement_accounts'},
    {'key': 'storefront', 'label': 'Storefront', 'icon': 'bi-shop-window', 'url_name': 'suppliers:storefront'},
    {'key': 'support', 'label': 'Support', 'icon': 'bi-headset', 'url_name': 'support:my_tickets'},
)

ADMIN_PARTNER_ITEMS = (
    {'match': 'user', 'label': 'Users', 'icon': 'bi-person', 'url_name': 'admin_panel:user_list'},
    {'match': 'customer', 'label': 'Customers', 'icon': 'bi-person-check', 'url_name': 'admin_panel:customer_list'},
    {'match': 'supplier', 'label': 'Suppliers', 'icon': 'bi-shop', 'url_name': 'admin_panel:supplier_list'},
    {'match': 'distributor', 'label': 'Distributors', 'icon': 'bi-truck', 'url_name': 'admin_panel:distributor_list'},
    {'match': 'stockist', 'label': 'Stockist', 'icon': 'bi-boxes', 'url_name': 'admin_panel:stockist_list'},
)

ADMIN_MAIN_ITEMS = (
    {'key': 'stock', 'match': 'stock_adjust', 'label': 'Stock', 'icon': 'bi-layers', 'url_name': 'admin_panel:stock_adjust'},
    {'key': 'products', 'match': 'product', 'exclude_match': 'marketplace', 'label': 'Products', 'icon': 'bi-box-seam', 'url_name': 'admin_panel:product_list'},
    {'key': 'marketplace', 'match': 'marketplace', 'label': 'Shop (Prices)', 'icon': 'bi-shop-window', 'url_name': 'admin_panel:marketplace'},
    {'key': 'orders', 'match': 'order', 'label': 'Orders', 'icon': 'bi-receipt', 'url_name': 'admin_panel:order_list'},
    {'key': 'categories', 'match': 'categor', 'label': 'Categories', 'icon': 'bi-tags', 'url_name': 'admin_panel:category_list'},
    {'key': 'reports', 'match': 'report', 'label': 'Reports', 'icon': 'bi-bar-chart', 'url_name': 'admin_panel:reports'},
    {'key': 'support', 'match': 'support', 'label': 'Support', 'icon': 'bi-headset', 'url_name': 'admin_panel:support_tickets'},
    {'key': 'rentals', 'match': 'rentals', 'label': 'Rentals', 'icon': 'bi-tools', 'url_name': 'rentals:admin_rentals'},
)