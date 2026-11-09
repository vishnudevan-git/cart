from django.urls import path

from . import views

app_name = 'rentals'

urlpatterns = [
    # Admin — the master switch
    path('admin/', views.admin_rentals, name='admin_rentals'),

    # Marketplace (shop)
    path('shop/', views.shop_items, name='shop_items'),
    path('shop/new/', views.shop_item_form, name='shop_item_create'),
    path('shop/<int:pk>/edit/', views.shop_item_form, name='shop_item_edit'),
    path('shop/<int:pk>/moderate/', views.shop_item_moderate, name='shop_item_moderate'),
    path('shop/<int:pk>/blocks/', views.shop_blocks, name='shop_blocks'),
    path('shop/bookings/', views.shop_bookings, name='shop_bookings'),
    path('shop/wallets/', views.shop_wallets, name='shop_wallets'),

    # Distributor (wholesale) — rents from its shops & publishes into them
    path('dist/', views.dist_list, name='dist_list'),
    path('dist/publish/', views.dist_publish, name='dist_publish'),
    path('dist/my-bookings/', views.dist_bookings, name='dist_bookings'),
    path('dist/<int:pk>/', views.dist_item, name='dist_item'),

    # Customer
    path('', views.customer_list, name='customer_list'),
    path('publish/', views.customer_publish, name='customer_publish'),
    path('my-bookings/', views.customer_bookings, name='customer_bookings'),
    path('my-items-bookings/', views.customer_owned_bookings, name='customer_owned_bookings'),
    path('<int:pk>/', views.customer_item, name='customer_item'),
    path('<int:pk>/blocks/', views.customer_blocks, name='customer_blocks'),
]
