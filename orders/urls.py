from django.urls import path
from . import views

app_name = 'orders'

urlpatterns = [
    path('checkout/', views.checkout, name='checkout'),
    path('my-orders/', views.order_list, name='order_list'),
    path('my-orders/<int:pk>/', views.order_detail, name='order_detail'),
    path('my-orders/<int:pk>/reorder/', views.reorder, name='reorder'),
    path('my-orders/<int:pk>/edit/', views.order_edit, name='order_edit'),
]
