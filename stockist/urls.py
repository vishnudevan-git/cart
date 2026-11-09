from django.urls import path
from . import views
from . import procurement_views as pv

app_name = 'stockist'

urlpatterns = [
    path('dashboard/', views.dashboard, name='dashboard'),
    path('inventory/', views.inventory, name='inventory'),
    path('processing-charges/', views.processing_charges, name='processing_charges'),
    path('processing-charges/overview/', views.charges_overview, name='charges_overview'),
    path('processing-charges/download/', views.charges_download, name='charges_download'),
    path('processing-charges/upload/', views.charges_upload, name='charges_upload'),
    path('restock-requests/', views.restock_requests, name='restock_requests'),
    path('orders/', views.order_list, name='order_list'),
    path('orders/<int:pk>/', views.order_detail, name='order_detail'),
    path('distributor-restock/', views.distributor_restock, name='distributor_restock'),
    path('direct-supply/', views.direct_supply, name='direct_supply'),
    # Procurement billing (stockist ↔ supplier — independent of customer orders)
    path('procurement/', pv.procurement_center, name='procurement_center'),
    path('procurement/account/<int:pk>/', pv.account_detail, name='account_detail'),
    path('procurement/invoice/create/', pv.create_invoice, name='create_invoice'),
    path('procurement/payment/create/', pv.create_payment, name='create_payment'),
    path('procurement/invoice/<int:pk>/cancel/', pv.cancel_invoice, name='cancel_invoice'),
]
