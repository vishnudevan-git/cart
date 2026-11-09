from django.urls import path

from . import wholesaler_views

app_name = 'wholesaler'

urlpatterns = [
    path('dashboard/', wholesaler_views.dashboard, name='dashboard'),
    path('shops/new/', wholesaler_views.create_shop, name='create_shop'),
    path('shops/<int:pk>/reset-password/', wholesaler_views.reset_shop_password, name='reset_shop_password'),
    path('my-stock/', wholesaler_views.my_stock, name='my_stock'),
]
