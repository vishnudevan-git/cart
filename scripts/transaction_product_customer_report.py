import os
import sys
from decimal import Decimal
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
  sys.path.insert(0, PROJECT_ROOT)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lakrish_mart.settings")

import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from accounts.models import CustomerAddress
from suppliers.models import Supplier
from distributors.models import Distributor, DistributorProduct, DistributorVariantPrice
from products.models import Category, Product, ProductVariant
from cart.models import Cart
from orders.models import Order


User = get_user_model()


def ensure_user(username, password, role, first_name, phone):
    user, created = User.objects.get_or_create(
        username=username,
        defaults={
            "first_name": first_name,
            "phone": phone,
            "is_active": True,
            "role": role,
            "roles": role,
        },
    )
    if created:
        user.set_password(password)
        user.save()
    else:
        changed = False
        if role not in user.get_roles_list():
            user.add_role(role)
            changed = True
        if not user.phone:
            user.phone = phone
            changed = True
        if changed:
            user.save()
    return user


def ensure_supplier():
    supplier_user = ensure_user(
        username="9000000001",
        password="Test@12345",
        role="supplier",
        first_name="Smoke",
        phone="9000000001",
    )
    supplier, _ = Supplier.objects.get_or_create(
        user=supplier_user,
        defaults={
            "company_name": "Smoke Supplier",
            "category": "organic",
            "status": "approved",
            "address": "Smoke Supplier Address",
        },
    )
    return supplier


def ensure_distributor():
    distributor_user = ensure_user(
        username="9000000002",
        password="Test@12345",
        role="distributor",
        first_name="Smoke",
        phone="9000000002",
    )
    distributor, _ = Distributor.objects.get_or_create(
        user=distributor_user,
        defaults={
            "company_name": "Smoke Distributor",
            "status": "active",
            "area": "Smoke Area",
            "address": "Smoke Distributor Address",
            "phone": "9000000002",
            "default_margin_percent": Decimal("12.00"),
            "delivery_charge": Decimal("30.00"),
            "free_delivery_above": Decimal("500.00"),
        },
    )
    if distributor.status != "active":
        distributor.status = "active"
        distributor.save(update_fields=["status"])
    return distributor


def ensure_customer(distributor):
    customer = ensure_user(
        username="9000000003",
        password="Test@12345",
        role="customer",
        first_name="SmokeCustomer",
        phone="9000000003",
    )
    if customer.distributor_id != distributor.id:
        customer.distributor = distributor
        customer.save(update_fields=["distributor"])

    address, _ = CustomerAddress.objects.get_or_create(
        user=customer,
        label="Home",
        defaults={
            "name": "Smoke Customer",
            "phone": customer.phone or customer.username,
            "door_number": "10A",
            "street_details": "Market Street",
            "locality": "Smoke Locality",
            "city": "Chennai",
            "pincode": "600001",
            "is_default": True,
        },
    )
    return customer, address


def create_product_and_price(supplier, distributor):
    category, _ = Category.objects.get_or_create(
        slug="smoke-checks",
        defaults={"name": "Smoke Checks", "code": "SC"},
    )

    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    product = Product.objects.create(
        supplier=supplier,
        category=category,
        name=f"Transaction Check Product {ts}",
        description="Automated product for transaction flow verification.",
        status="approved",
        charges_confirmed=True,
        show_mrp=True,
    )

    variant = ProductVariant.objects.create(
        product=product,
        label="500g",
        mrp=Decimal("249.00"),
        selling_price=Decimal("199.00"),
        stock=25,
        is_active=True,
        unit_grams=500,
    )

    DistributorProduct.objects.update_or_create(
        distributor=distributor,
        product=product,
        defaults={
            "is_listed": True,
            "margin_percent": Decimal("10.00"),
            "margin_mode": "percent",
        },
    )

    distributor_price = Decimal("219.00")
    DistributorVariantPrice.objects.update_or_create(
        distributor=distributor,
        variant=variant,
        defaults={
            "base_price": variant.selling_price,
            "selling_price": distributor_price,
            "is_active": True,
            "price_stale": False,
        },
    )

    return product, variant, distributor_price


def run_customer_flow(customer, address, product, variant):
    client = Client()
    client.force_login(customer)
    session = client.session
    session["active_role"] = "customer"
    session.save()

    checks = []

    def check_get(label, route_name, kwargs=None):
        url = reverse(route_name, kwargs=kwargs or {})
        response = client.get(url)
        checks.append(
            {
                "step": label,
                "url": url,
                "status": response.status_code,
                "ok": response.status_code == 200,
            }
        )
        return url, response

    check_get("Customer Home", "products:home")
    check_get("Customer Product List", "products:product_list")
    detail_url, detail_resp = check_get("Customer Product Detail", "products:product_detail", {"pk": product.pk})

    add_url = reverse("cart:add", kwargs={"variant_pk": variant.pk})
    add_resp = client.post(
        add_url,
        {"quantity": 2},
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )
    add_json = {}
    try:
        add_json = add_resp.json()
    except Exception:
        add_json = {"raw": add_resp.content.decode("utf-8", errors="ignore")[:200]}

    checks.append(
        {
            "step": "Add To Cart (AJAX)",
            "url": add_url,
            "status": add_resp.status_code,
            "ok": add_resp.status_code == 200 and add_json.get("success") is True,
        }
    )

    cart_url, cart_resp = check_get("Customer Cart View", "cart:cart")

    checkout_url = reverse("orders:checkout")
    checkout_post_resp = client.post(
        checkout_url,
        {
            "address_id": str(address.pk),
            "phone": customer.phone or customer.username,
            "payment_method": "cod",
        },
        follow=False,
    )

    checkout_ok = checkout_post_resp.status_code in (302, 303)
    redirect_location = checkout_post_resp.headers.get("Location", "")

    order = Order.objects.filter(customer=customer).order_by("-id").first()

    checks.append(
        {
            "step": "Checkout POST (Create Order)",
            "url": checkout_url,
            "status": checkout_post_resp.status_code,
            "ok": checkout_ok and bool(order),
        }
    )

    order_detail_status = None
    order_detail_url = ""
    if order:
        order_detail_url = reverse("orders:order_detail", kwargs={"pk": order.pk})
        order_detail_resp = client.get(order_detail_url)
        order_detail_status = order_detail_resp.status_code
        checks.append(
            {
                "step": "Customer Order Detail",
                "url": order_detail_url,
                "status": order_detail_status,
                "ok": order_detail_status == 200,
            }
        )

    payments_url, payments_resp = check_get("Customer Payments Center", "payments:center")

    in_cart_qty = Cart.objects.filter(customer=customer, variant=variant).values_list("quantity", flat=True).first()

    flow_data = {
        "detail_page_contains_product": product.name in detail_resp.content.decode("utf-8", errors="ignore"),
        "cart_page_status": cart_resp.status_code,
        "payments_page_status": payments_resp.status_code,
        "add_to_cart_response": add_json,
        "checkout_redirect": redirect_location,
        "in_cart_qty_after_checkout": in_cart_qty,
    }

    return checks, flow_data, order


def render_html(report):
    checks_rows = "\n".join(
        f"<tr><td>{c['step']}</td><td><a href=\"{c['url']}\" target=\"_blank\">{c['url']}</a></td><td>{c['status']}</td><td class=\"{'ok' if c['ok'] else 'fail'}\">{'PASS' if c['ok'] else 'FAIL'}</td></tr>"
        for c in report["checks"]
    )

    details_json = str(report["flow_data"]).replace("<", "&lt;").replace(">", "&gt;")

    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Transaction Product Customer Flow Report</title>
  <style>
    :root {{
      --bg: #fff8ee;
      --panel: #ffffff;
      --text: #1f2937;
      --muted: #6b7280;
      --brand: #0f766e;
      --brand-2: #f59e0b;
      --ok: #166534;
      --ok-bg: #dcfce7;
      --fail: #991b1b;
      --fail-bg: #fee2e2;
      --line: #fde7c7;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--text);
      background:
        radial-gradient(circle at 10% 10%, #ffe8c2 0%, transparent 25%),
        radial-gradient(circle at 90% 0%, #c7f9f3 0%, transparent 30%),
        var(--bg);
      font-family: "Segoe UI", "Trebuchet MS", sans-serif;
    }}
    .wrap {{ max-width: 1100px; margin: 24px auto 48px; padding: 0 16px; }}
    .hero {{
      background: linear-gradient(120deg, #0f766e, #0d9488 45%, #f59e0b 120%);
      color: #fff;
      border-radius: 18px;
      padding: 26px;
      box-shadow: 0 16px 40px rgba(15, 118, 110, 0.25);
      animation: dropIn 700ms ease;
    }}
    .hero h1 {{ margin: 0 0 10px; font-size: 1.7rem; }}
    .hero p {{ margin: 0; opacity: 0.95; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
      margin: 14px 0 18px;
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
      box-shadow: 0 8px 24px rgba(0,0,0,0.06);
    }}
    .k {{ color: var(--muted); font-size: 0.85rem; text-transform: uppercase; letter-spacing: .06em; }}
    .v {{ margin-top: 6px; font-size: 1.1rem; font-weight: 700; }}
    .toolbar {{ display: flex; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }}
    .btn {{
      border: 1px solid #0f766e;
      background: #ecfeff;
      color: #0f766e;
      padding: 8px 12px;
      border-radius: 999px;
      cursor: pointer;
      font-weight: 600;
    }}
    .btn.active {{ background: #0f766e; color: #fff; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border-radius: 12px; overflow: hidden; }}
    th, td {{ padding: 10px; border-bottom: 1px solid #f3f4f6; text-align: left; font-size: 0.93rem; }}
    th {{ background: #f8fafc; font-size: .82rem; letter-spacing: .04em; text-transform: uppercase; color: #475569; }}
    tr:hover td {{ background: #fffaf0; }}
    .ok {{ color: var(--ok); background: var(--ok-bg); border-radius: 999px; padding: 2px 8px; display: inline-block; font-weight: 700; }}
    .fail {{ color: var(--fail); background: var(--fail-bg); border-radius: 999px; padding: 2px 8px; display: inline-block; font-weight: 700; }}
    pre {{
      background: #0f172a;
      color: #e2e8f0;
      padding: 12px;
      border-radius: 12px;
      overflow-x: auto;
      line-height: 1.4;
      font-size: .9rem;
    }}
    @keyframes dropIn {{
      from {{ transform: translateY(-10px); opacity: 0; }}
      to {{ transform: translateY(0); opacity: 1; }}
    }}
  </style>
</head>
<body>
  <div class=\"wrap\">
    <section class=\"hero\">
      <h1>Single Transaction Check: Product to Customer View</h1>
      <p>Generated on {report['generated_at']} | Result: {report['pass_count']} passed / {report['total_checks']} total</p>
    </section>

    <section class=\"grid\">
      <div class=\"card\"><div class=\"k\">Supplier</div><div class=\"v\">{report['supplier_company']}</div></div>
      <div class=\"card\"><div class=\"k\">Distributor</div><div class=\"v\">{report['distributor_company']}</div></div>
      <div class=\"card\"><div class=\"k\">Customer</div><div class=\"v\">{report['customer_username']}</div></div>
      <div class=\"card\"><div class=\"k\">Product</div><div class=\"v\">{report['product_name']}</div></div>
      <div class=\"card\"><div class=\"k\">Base Price</div><div class=\"v\">Rs. {report['variant_selling_price']}</div></div>
      <div class=\"card\"><div class=\"k\">Customer Price</div><div class=\"v\">Rs. {report['distributor_price']}</div></div>
      <div class=\"card\"><div class=\"k\">Order ID</div><div class=\"v\">{report['order_id'] or 'Not Created'}</div></div>
    </section>

    <section class=\"card\">
      <h2>Checks</h2>
      <div class=\"toolbar\">
        <button class=\"btn active\" data-filter=\"all\">All</button>
        <button class=\"btn\" data-filter=\"pass\">Pass</button>
        <button class=\"btn\" data-filter=\"fail\">Fail</button>
      </div>
      <table id=\"checksTable\">
        <thead>
          <tr><th>Step</th><th>URL</th><th>Status</th><th>Result</th></tr>
        </thead>
        <tbody>
          {checks_rows}
        </tbody>
      </table>
    </section>

    <section class=\"card\" style=\"margin-top:12px;\">
      <h2>Raw Flow Output</h2>
      <pre>{details_json}</pre>
    </section>
  </div>

  <script>
    const buttons = document.querySelectorAll('.btn');
    const rows = Array.from(document.querySelectorAll('#checksTable tbody tr'));

    function applyFilter(mode) {{
      rows.forEach((row) => {{
        const isPass = row.querySelector('.ok') !== null;
        if (mode === 'all') row.style.display = '';
        if (mode === 'pass') row.style.display = isPass ? '' : 'none';
        if (mode === 'fail') row.style.display = isPass ? 'none' : '';
      }});
    }}

    buttons.forEach((btn) => {{
      btn.addEventListener('click', () => {{
        buttons.forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');
        applyFilter(btn.dataset.filter);
      }});
    }});
  </script>
</body>
</html>
"""


def main():
    supplier = ensure_supplier()
    distributor = ensure_distributor()
    customer, address = ensure_customer(distributor)
    product, variant, distributor_price = create_product_and_price(supplier, distributor)

    checks, flow_data, order = run_customer_flow(customer, address, product, variant)

    pass_count = sum(1 for c in checks if c["ok"])
    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "supplier_company": supplier.company_name,
        "distributor_company": distributor.company_name,
        "customer_username": customer.username,
        "product_name": product.name,
        "variant_label": variant.label,
        "variant_selling_price": str(variant.selling_price),
        "distributor_price": str(distributor_price),
        "order_id": order.id if order else None,
        "checks": checks,
        "flow_data": flow_data,
        "total_checks": len(checks),
        "pass_count": pass_count,
    }

    html = render_html(report)
    output_file = "transaction_customer_flow_report.html"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(html)

    print("TRANSACTION CHECK COMPLETE")
    print(f"Report file: {output_file}")
    print(f"Product created: {product.name} (Variant: {variant.label})")
    print(f"Pricing: base={variant.selling_price} customer={distributor_price}")
    print(f"Order created: {order.id if order else 'NO'}")
    print(f"Checks passed: {pass_count}/{len(checks)}")


if __name__ == "__main__":
    main()
