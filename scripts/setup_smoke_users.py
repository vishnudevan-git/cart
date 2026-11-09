"""Provision the isolated accounts used by scripts/url_smoke_audit.py.

Creates one account for each authenticated role plus the profiles required by
that role. It never attempts to assign multiple roles to one account.

PowerShell:
  venv\Scripts\python.exe scripts\setup_smoke_users.py

Accounts created or repaired (all password: Test@12345):
  9000000001 supplier
  9000000002 Marketplace (code role: distributor)
  9000000003 customer, linked to the Marketplace
  9000000004 stockist
  9000000005 admin
  9000000009 Distributor (code role: wholesaler), linked to the stockist
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lakrish_mart.settings")

import django
django.setup()

from accounts.models import User
from distributors.models import Distributor
from distributors.wholesaler_models import Wholesaler
from stockist.models import Stockist
from suppliers.models import Supplier

PASSWORD = "Test@12345"


def ensure_user(username, role):
    """Create or normalize this dedicated smoke account to exactly one role."""
    user = User.all_objects.filter(username=username).first()
    if user is None:
        user = User.objects.create_user(
            username=username,
            password=PASSWORD,
            phone=username,
            role=role,
            roles=role,
            is_active=True,
        )
    else:
        user.set_roles([role])
        user.is_active = True
        user.is_deleted = False
        user.deleted_at = None
        user.deleted_by = ""
        user.save()
    return user


def main():
    supplier_user = ensure_user("9000000001", "supplier")
    Supplier.objects.get_or_create(
        user=supplier_user,
        defaults={
            "company_name": "Smoke Supplier",
            "category": "organic",
            "status": "approved",
            "address": "Smoke Supplier Address",
        },
    )

    stockist_user = ensure_user("9000000004", "stockist")
    stockist, _ = Stockist.objects.get_or_create(
        user=stockist_user,
        defaults={
            "company_name": "Smoke Stockist",
            "status": "active",
            "area": "Smoke Area",
            "address": "Smoke Stockist Address",
            "phone": "9000000004",
        },
    )

    distributor_user = ensure_user("9000000002", "distributor")
    distributor, _ = Distributor.objects.get_or_create(
        user=distributor_user,
        defaults={
            "company_name": "Smoke Marketplace",
            "status": "active",
            "area": "Smoke Area",
            "address": "Smoke Marketplace Address",
            "phone": "9000000002",
        },
    )

    wholesaler_user = ensure_user("9000000009", "wholesaler")
    wholesaler, _ = Wholesaler.objects.get_or_create(
        user=wholesaler_user,
        defaults={
            "company_name": "Smoke Distributor",
            "status": "active",
            "area": "Smoke Area",
            "phone": "9000000009",
            "stockist": stockist,
        },
    )
    if wholesaler.stockist_id != stockist.pk:
        wholesaler.stockist = stockist
        wholesaler.save(update_fields=["stockist"])
    if distributor.wholesaler_id != wholesaler.pk:
        distributor.wholesaler = wholesaler
        distributor.save(update_fields=["wholesaler"])

    customer_user = ensure_user("9000000003", "customer")
    if customer_user.distributor_id != distributor.pk:
        customer_user.distributor = distributor
        customer_user.save(update_fields=["distributor"])

    admin_user = ensure_user("9000000005", "admin")
    if not admin_user.is_superuser or not admin_user.is_staff:
        admin_user.is_superuser = True
        admin_user.is_staff = True
        admin_user.save(update_fields=["is_superuser", "is_staff"])

    print("Smoke users and required role profiles are ready.")


if __name__ == "__main__":
    main()
