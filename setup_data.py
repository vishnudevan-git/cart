"""
Run this once to create the admin user and seed initial categories:
  python setup_data.py
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'lakrish_mart.settings')
django.setup()

from accounts.models import User
from products.models import Category

# Create admin user
if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser(
        username='admin',
        email='admin@lakrish.com',
        password='admin123',
        role='admin',
    )
    print("Admin user created: admin / admin123")
else:
    print("Admin user already exists.")

# Seed categories
categories = [
    ('Organic Products', 'organic-products'),
    ('Dry Fruits', 'dry-fruits'),
    ('Nuts', 'nuts'),
    ('Spices', 'spices'),
    ('Other', 'other'),
]
for name, slug in categories:
    cat, created = Category.objects.get_or_create(slug=slug, defaults={'name': name})
    if created:
        print(f"Category created: {name}")

print("Setup complete.")
