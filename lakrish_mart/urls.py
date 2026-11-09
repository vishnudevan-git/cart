from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.conf.urls.i18n import i18n_patterns
import django.views.i18n as i18n_views
from django.views.generic import TemplateView
from admin_panel.views import project_runbook, product_lifecycle_api, supplier_shipment_api

urlpatterns = [
    path('i18n/', include('django.conf.urls.i18n')),
    path('runbook/', project_runbook, name='project_runbook'),
    path('product-lifecycle/', TemplateView.as_view(template_name='project/price_track_validator.html'), name='product_lifecycle'),
    path('product-lifecycle/api/', product_lifecycle_api, name='product_lifecycle_api'),
    path('supplier-shipment/', TemplateView.as_view(template_name='project/supplier_shipment_explainer.html'), name='supplier_shipment'),
    path('supplier-shipment/api/', supplier_shipment_api, name='supplier_shipment_api'),
    path('django-admin/', admin.site.urls),
    path('robots.txt', TemplateView.as_view(template_name='robots.txt', content_type='text/plain')),
    path('terms/', TemplateView.as_view(template_name='legal/terms.html'), name='terms'),
    path('privacy/', TemplateView.as_view(template_name='legal/privacy.html'), name='privacy'),
    path('', include('products.urls')),
    path('accounts/', include('accounts.urls')),
    path('supplier/', include('suppliers.urls')),
    path('cart/', include('cart.urls')),
    path('orders/', include('orders.urls')),
    path('payments/', include('payments.urls')),
    path('admin-panel/', include('admin_panel.urls')),
    path('delivery/', include('delivery.urls')),
    path('distributor/', include('distributors.urls')),
    path('wholesale/', include('distributors.wholesaler_urls')),
    path('support/', include('support.urls')),
    path('stockist/', include('stockist.urls')),
    path('transactions/', include('transactions.urls')),
    path('rentals/', include('rentals.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
