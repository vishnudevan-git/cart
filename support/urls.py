from django.urls import path
from . import views

app_name = 'support'

urlpatterns = [
    path('submit/', views.submit_ticket, name='submit_ticket'),
    path('my-tickets/', views.my_tickets, name='my_tickets'),
    path('my-tickets/<int:pk>/', views.ticket_detail, name='ticket_detail'),
]
