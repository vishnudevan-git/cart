import os
import sys
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lakrish_mart.settings")

import django

django.setup()

from django.test import Client
from django.urls import URLPattern, URLResolver, get_resolver, reverse

from accounts.models import User


REQUIRED_ROLES = ("customer", "supplier", "distributor", "wholesaler", "stockist", "admin")


CONVERTER_DEFAULTS = {
    "int": 1,
    "slug": "test",
    "str": "test",
    "uuid": "123e4567-e89b-12d3-a456-426614174000",
    "path": "test/path",
}


def walk_patterns(patterns, namespace=""):
    names = []
    for pattern in patterns:
        if isinstance(pattern, URLPattern):
            if pattern.name:
                full_name = f"{namespace}:{pattern.name}" if namespace else pattern.name
                names.append((full_name, pattern))
        elif isinstance(pattern, URLResolver):
            ns = namespace
            if pattern.namespace:
                ns = f"{namespace}:{pattern.namespace}" if namespace else pattern.namespace
            names.extend(walk_patterns(pattern.url_patterns, ns))
    return names


def build_kwargs(pattern):
    kwargs = {}
    for key, converter in getattr(pattern.pattern, "converters", {}).items():
        converter_name = converter.__class__.__name__.replace("Converter", "").lower()
        kwargs[key] = CONVERTER_DEFAULTS.get(converter_name, "test")
    return kwargs


def pick_user_for_role(role):
    if role == "admin":
        return User.objects.filter(is_active=True, is_superuser=True).first() or User.objects.filter(
            is_active=True, role="admin"
        ).first()

    for user in User.objects.filter(is_active=True):
        if role in user.get_roles_list():
            return user
    return None


def get_with_role(client, user, role, url):
    client.force_login(user)
    session = client.session
    session["active_role"] = role
    session.save()
    return client.get(url)


def main():
    resolver = get_resolver()
    named_patterns = walk_patterns(resolver.url_patterns)

    reverse_failures = []
    unresolved_dynamic = []
    resolved_urls = []

    for name, pattern in named_patterns:
        kwargs = build_kwargs(pattern)
        try:
            if kwargs:
                url = reverse(name, kwargs=kwargs)
            else:
                url = reverse(name)
            resolved_urls.append((name, url))
        except Exception as exc:
            # Known dynamic admin app list uses regex groups not exposed as converters.
            if name == "admin:app_list":
                unresolved_dynamic.append((name, str(exc)))
            else:
                reverse_failures.append((name, kwargs, str(exc)))

    anon_client = Client()
    anon_500 = []
    for name, url in resolved_urls:
        try:
            response = anon_client.get(url)
            if response.status_code >= 500:
                anon_500.append((name, url, response.status_code))
        except Exception as exc:
            anon_500.append((name, url, f"EXCEPTION: {exc}"))

    role_clients = {role: Client() for role in REQUIRED_ROLES}
    role_users = {role: pick_user_for_role(role) for role in role_clients}
    missing_role_users = [role for role, user in role_users.items() if not user]
    role_500 = defaultdict(list)

    for role, user in role_users.items():
        if not user:
            continue
        client = role_clients[role]
        for name, url in resolved_urls:
            try:
                response = get_with_role(client, user, role, url)
                if response.status_code >= 500:
                    role_500[role].append((name, url, response.status_code))
            except Exception as exc:
                role_500[role].append((name, url, f"EXCEPTION: {exc}"))

    flow_checks = []

    # Flow: account registration page + minimal POST (expect non-500)
    try:
        register_url = reverse("accounts:register")
        c = Client()
        flow_checks.append(("register_get", register_url, c.get(register_url).status_code))
        flow_checks.append(
            (
                "register_post_minimal",
                register_url,
                c.post(
                    register_url,
                    {
                        "username": "9999999999",
                        "password1": "demo-pass-123",
                        "password2": "demo-pass-123",
                    },
                ).status_code,
            )
        )
    except Exception as exc:
        flow_checks.append(("register_flow", "ERR", str(exc)))

    # Flow: supplier add product page + minimal POST
    supplier_user = role_users.get("supplier")
    if supplier_user:
        c = Client()
        try:
            u = reverse("suppliers:product_add")
            flow_checks.append(("supplier_add_product_get", u, get_with_role(c, supplier_user, "supplier", u).status_code))
            flow_checks.append(("supplier_add_product_post_minimal", u, c.post(u, {}).status_code))
        except Exception as exc:
            flow_checks.append(("supplier_add_product", "ERR", str(exc)))
    else:
        flow_checks.append(("supplier_add_product", "SKIP", "No active supplier user found"))

    # Flow: order pages as customer
    customer_user = role_users.get("customer")
    if customer_user:
        c = Client()
        for flow_name, route_name in [
            ("order_checkout_get", "orders:checkout"),
            ("order_list_get", "orders:order_list"),
        ]:
            try:
                u = reverse(route_name)
                flow_checks.append((flow_name, u, get_with_role(c, customer_user, "customer", u).status_code))
            except Exception as exc:
                flow_checks.append((flow_name, "ERR", str(exc)))
    else:
        flow_checks.append(("order_flow", "SKIP", "No active customer user found"))

    print("=== URL AUDIT SUMMARY ===")
    print("TOTAL_NAMED_URLS", len(named_patterns))
    print("TOTAL_REVERSED", len(resolved_urls))
    print("REVERSE_FAILURES", len(reverse_failures))
    print("KNOWN_DYNAMIC_UNRESOLVED", len(unresolved_dynamic))
    print("ANON_500", len(anon_500))
    for role in REQUIRED_ROLES:
        user = role_users.get(role)
        print(f"ROLE_USER_{role.upper()}", bool(user))
        print(f"ROLE_500_{role.upper()}", len(role_500.get(role, [])))
    print("MISSING_ROLE_USERS", ', '.join(missing_role_users) if missing_role_users else 'none')

    if reverse_failures:
        print("=== REVERSE FAILURES ===")
        for row in reverse_failures[:200]:
            print(row)

    if anon_500:
        print("=== ANON 500s ===")
        for row in anon_500[:200]:
            print(row)

    for role, rows in role_500.items():
        if rows:
            print(f"=== ROLE 500s: {role} ===")
            for row in rows[:200]:
                print(row)

    print("=== FLOW CHECKS ===")
    for row in flow_checks:
        print(row)

    # A skipped role means this was not an all-role audit. Make that visible to
    # automation instead of silently reporting a misleading success.
    return 1 if missing_role_users else 0


if __name__ == "__main__":
    sys.exit(main())