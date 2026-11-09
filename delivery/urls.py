from django.urls import path
from . import api_views, views

app_name = 'delivery'

urlpatterns = [
    # Web views for distributors/admin
    path('', views.delivery_list, name='delivery_list'),
    path('<int:pk>/', views.delivery_detail, name='delivery_detail'),
    path('<int:pk>/out-for-delivery/', views.mark_out_for_delivery, name='mark_out_for_delivery'),
    path('<int:pk>/mark-delivered/', views.mark_delivered, name='mark_delivered'),
    path('<int:pk>/partial-deliver/', views.mark_partial_delivery, name='mark_partial_delivery'),

    # Mobile App API Endpoints
    path('api/login/', api_views.CustomAuthToken.as_view(), name='api_login'),
    path('api/orders/', api_views.DeliveryOrderListAPI.as_view(), name='api_order_list'),
    path('api/orders/<int:pk>/update/', api_views.DeliveryOrderUpdateAPI.as_view(), name='api_order_update'),
]
