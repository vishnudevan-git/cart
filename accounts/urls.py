from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('demo-login/', views.demo_login, name='demo_login'),
    path('login/password/', views.login_password, name='login_password'),
    path('logout/', views.logout_view, name='logout'),
    path('quick-test-login/', views.quick_test_login, name='quick_test_login'),
    path('register/prompt/', views.register_prompt, name='register_prompt'),
    path('register/', views.register_customer, name='register'),
    path('profile/', views.profile, name='profile'),
    # Single-role policy: no role switching. pick-role is a redirect-only shim.
    path('pick-role/', views.pick_role, name='pick_role'),
    path('forgot-password/', views.forgot_password, name='forgot_password'),
    path('change-password/', views.change_password, name='change_password'),
    path('request-password-reset/', views.request_password_reset, name='request_password_reset'),
    path('preferences/', views.save_preferences, name='save_preferences'),
    path('toggle-dark/', views.toggle_dark_mode, name='toggle_dark'),
    path('addresses/', views.manage_addresses, name='addresses'),
    path('addresses/delete/<int:pk>/', views.delete_address, name='delete_address'),
    path('rewards/', views.my_rewards, name='my_rewards'),
    path('pincode/<str:pincode>/', views.pincode_lookup, name='pincode_lookup'),
]
