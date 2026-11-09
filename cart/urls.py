from django.urls import path
from . import views

app_name = 'cart'

urlpatterns = [
    path('', views.cart_view, name='cart'),
    path('add/<int:variant_pk>/', views.add_to_cart, name='add'),
    path('update/<int:pk>/', views.update_cart, name='update'),
    path('remove/<int:pk>/', views.remove_from_cart, name='remove'),
    path('api/state/', views.cart_state_api, name='api_state'),
    path('api/set_qty/<int:variant_pk>/', views.set_qty_api, name='api_set_qty'),
    path('api/offcanvas/', views.cart_offcanvas_view, name='api_offcanvas'),
]
