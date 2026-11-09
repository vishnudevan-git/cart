"""
Seed a full demo network onto the current DB (additive, no deletes):

  7 Suppliers
  1 Stockist  (reuses an existing active stockist; all wholesalers route to it)
  4 Distributors (wholesale tier = Wholesaler), each owning 5 shops  => 20 shops
  4-5 Customers per shop  (each linked to its shop)

All seeded accounts use password:  Test@12345

Run:  venv\Scripts\python.exe scripts\seed_network.py
"""
import os
import sys
import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lakrish_mart.settings")
django.setup()

from django.db import transaction
from accounts.models import User
from suppliers.models import Supplier
from stockist.models import Stockist
from distributors.models import Distributor
from distributors.wholesaler_models import Wholesaler

PASSWORD = "Test@12345"

# Phone-number namespace for this seed (so it won't collide with existing data).
# Suppliers:   9700000001..07
# Stockist:    (reuse existing)
# Wholesalers: 9710000001..04
# Shops:       971100xxyy  (xx = wholesaler index, yy = shop index)
# Customers:   972wwss nn   built per shop below

SUPPLIERS = [
    ("Green Valley Farms",   "organic",    "9700000001"),
    ("Nutty Delights Co",    "nuts",       "9700000002"),
    ("Spice Route Traders",  "spices",     "9700000003"),
    ("Sunrise Dry Fruits",   "dry_fruits", "9700000004"),
    ("Pure Harvest Organics","organic",    "9700000005"),
    ("Kerala Spice House",   "spices",     "9700000006"),
    ("Royal Cashew Exports", "nuts",       "9700000007"),
]

# 4 wholesale Distributors + their territories. margin by quantity/weight.
WHOLESALERS = [
    ("North Chennai Wholesale",  "North Chennai",  "per_quantity", 8,  "9710000001"),
    ("South Chennai Wholesale",  "South Chennai",  "per_weight",   40, "9710000002"),
    ("West Chennai Wholesale",   "West Chennai",   "per_quantity", 6,  "9710000003"),
    ("Coimbatore Wholesale",     "Coimbatore",     "per_weight",   35, "9710000004"),
]

# 5 shop name templates per wholesaler (filled with area)
SHOP_NAMES = [
    "{area} Fresh Mart",
    "{area} Daily Needs",
    "{area} Super Store",
    "{area} Organic Bazaar",
    "{area} Family Grocers",
]

FIRST_NAMES = ["Arun", "Divya", "Karthik", "Lakshmi", "Mohan",
               "Nithya", "Prakash", "Revathi", "Suresh", "Vani"]
LAST_NAMES = ["Kumar", "Raj", "Nair", "Iyer", "Pillai", "Menon"]


def get_or_make_user(username, first_name, role, phone, email):
    u = User.objects.filter(username=username).first()
    if u:
        return u, False
    u = User.objects.create_user(
        username=username, password=PASSWORD,
        first_name=first_name, phone=phone, email=email, role=role,
    )
    return u, True


@transaction.atomic
def run():
    report = {"suppliers": 0, "shops": 0, "customers": 0, "wholesalers": 0}

    # ── 1. One stockist: reuse an existing active one (request = "one stockist")
    stockist = Stockist.objects.filter(status="active").first()
    if not stockist:
        su, _ = get_or_make_user("9700009999", "Central", "stockist",
                                 "9700009999", "central.stockist@demo.com")
        stockist = Stockist.objects.create(
            user=su, company_name="Central City Stockist",
            area="Chennai Hub", phone="9700009999", status="active")
    print(f"Stockist (all wholesalers route here): {stockist.company_name}")

    # ── 2. Suppliers
    for name, cat, phone in SUPPLIERS:
        uname = phone
        u, created = get_or_make_user(uname, name.split()[0], "supplier",
                                      phone, f"{phone}@demo.com")
        sup, s_created = Supplier.all_objects.get_or_create(
            user=u, defaults={"company_name": name, "category": cat,
                              "status": "approved"})
        if not sup.company_name:
            sup.company_name = name
            sup.save()
        if created or s_created:
            report["suppliers"] += 1
            print(f"  Supplier: {name} [{uname}]")

    # ── 3. Wholesalers (4), each with 5 shops, each shop with 4-5 customers
    for wi, (wname, warea, mode, mval, wphone) in enumerate(WHOLESALERS, start=1):
        wu, _ = get_or_make_user(wphone, wname.split()[0], "wholesaler",
                                 wphone, f"{wphone}@demo.com")
        wholesaler, w_created = Wholesaler.objects.get_or_create(
            user=wu,
            defaults={"company_name": wname, "area": warea, "phone": wphone,
                      "status": "active", "margin_mode": mode,
                      "margin_value": mval, "stockist": stockist})
        if not wholesaler.stockist_id:
            wholesaler.stockist = stockist
            wholesaler.save(update_fields=["stockist"])
        if w_created:
            report["wholesalers"] += 1
        print(f"\nDistributor (wholesale): {wname}  margin={wholesaler.margin_display()}  -> {stockist.company_name}")

        for si in range(1, 6):  # 5 shops
            shop_phone = f"9711{wi:02d}{si:02d}00"
            shop_name = SHOP_NAMES[si - 1].format(area=warea)
            shu, _ = get_or_make_user(shop_phone, shop_name.split()[0],
                                      "distributor", shop_phone, f"{shop_phone}@demo.com")
            shop, sh_created = Distributor.objects.get_or_create(
                user=shu,
                defaults={"company_name": shop_name, "area": warea,
                          "phone": shop_phone, "status": "active",
                          "wholesaler": wholesaler, "delivery_charge": 25,
                          "free_delivery_above": 400, "default_margin_percent": 10})
            if shop.wholesaler_id != wholesaler.id:
                shop.wholesaler = wholesaler
                shop.save(update_fields=["wholesaler"])
            if sh_created:
                report["shops"] += 1
            print(f"  Shop: {shop_name} [{shop_phone}]  code={shop.invite_code}")

            # 4-5 customers per shop
            n_cust = 4 + (si % 2)  # alternates 4 and 5
            for ci in range(1, n_cust + 1):
                # 10-digit unique username: 972 + w(1) + s(1) + cc(2) + 11
                #   e.g. wholesaler 1, shop 1, customer 1 -> 9721101 11 = 9721101 11
                cust_phone = f"972{wi}{si}{ci:02d}11"
                fname = FIRST_NAMES[(wi * si * ci) % len(FIRST_NAMES)]
                lname = LAST_NAMES[(wi + si + ci) % len(LAST_NAMES)]
                cu = User.objects.filter(username=cust_phone).first()
                if not cu:
                    cu = User.objects.create_user(
                        username=cust_phone, password=PASSWORD,
                        first_name=fname, last_name=lname,
                        phone=cust_phone, email=f"{cust_phone}@demo.com",
                        role="customer", distributor=shop)
                    report["customers"] += 1
                else:
                    if not cu.distributor_id:
                        cu.distributor = shop
                        cu.save(update_fields=["distributor"])
            print(f"      + {n_cust} customers")

    print("\n================ SEED COMPLETE ================")
    print(f"  new suppliers:   {report['suppliers']}")
    print(f"  new wholesalers: {report['wholesalers']}")
    print(f"  new shops:       {report['shops']}")
    print(f"  new customers:   {report['customers']}")
    print(f"  all passwords:   {PASSWORD}")


if __name__ == "__main__":
    run()
