from django.urls import path
from . import views

app_name = 'products'

urlpatterns = [
    path('', views.home, name='home'),
    path('store/<str:invite_code>/', views.distributor_storefront, name='distributor_storefront'),
    path('products/', views.product_list, name='product_list'),
    path('products/<int:pk>/', views.product_detail, name='product_detail'),
    path('join/<str:invite_code>/', views.set_distributor, name='set_distributor'),
    path('api/search/', views.search_autocomplete, name='search_autocomplete'),
    path('products/<int:pk>/review/', views.add_review, name='add_review'),
]
