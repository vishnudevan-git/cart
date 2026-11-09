"""
Test fixtures — builds the whole 5-tier world in one call.

Every test that needs "a shop, a rival shop, a customer of each, and someone
upstream" gets it from :func:`build_world` instead of re-creating six users by
hand. Keeping it in one place means a model change breaks one file, not twelve.

Naming map (the codebase and the UI disagree, deliberately):
    role 'distributor' = UI "Marketplace" (a shop)
    role 'wholesaler'  = UI "Distributor" (the wholesale tier)

Not imported by production code — only by tests.
"""
from types import SimpleNamespace

from django.contrib.auth import get_user_model

User = get_user_model()

PASSWORD = 'test-pass-123'


def make_user(username, role, **extra):
    """A user holding exactly ONE role (single-role policy)."""
    user = User.objects.create_user(username=username, password=PASSWORD, **extra)
    user.set_roles([role])
    user.save()
    return user


def make_category(name='Dry Fruits', slug='dry-fruits', code='DF'):
    from products.models import Category
    return Category.objects.create(name=name, slug=slug, code=code)


def make_supplier(username='supplier@test.com', company='Green Farms'):
    from suppliers.models import Supplier
    user = make_user(username, 'supplier')
    profile = Supplier.objects.create(
        user=user, company_name=company, category='organic', status='approved'
    )
    return user, profile


def make_enterprise(username, company, **extra):
    """A shop. Returns (user, Distributor profile)."""
    from distributors.models import Distributor
    user = make_user(username, 'distributor')
    profile = Distributor.objects.create(
        user=user, company_name=company, status='active', **extra
    )
    user.refresh_from_db()
    return user, profile


def make_customer(username, enterprise):
    """A shopper who buys from exactly one shop."""
    return make_user(username, 'customer', distributor=enterprise)


def make_shared_product(supplier, category, name='Marketplace Cashew',
                        price='500', stock=100, **extra):
    """An ordinary marketplace product — no owner_enterprise, everyone sees it."""
    from products.models import Product, ProductVariant
    defaults = {'status': 'approved', 'charges_confirmed': True}
    defaults.update(extra)
    product = Product.objects.create(
        supplier=supplier, category=category, name=name, **defaults
    )
    ProductVariant.objects.create(
        product=product, label='500 g', selling_price=price, stock=stock, unit_grams=500
    )
    return product


def build_world():
    """One shop-owner per role, two rival shops, a customer under each.

    Returns a namespace so tests read as ``world.shop_a`` rather than tuple
    unpacking six values.
    """
    from distributors.wholesaler_models import Wholesaler
    from stockist.models import Stockist

    category = make_category()

    admin = make_user('admin@test.com', 'admin')

    stockist_user = make_user('stockist@test.com', 'stockist')
    stockist = Stockist.objects.create(
        user=stockist_user, company_name='City Stockist', status='active'
    )

    wholesaler_user = make_user('wholesaler@test.com', 'wholesaler')
    wholesaler = Wholesaler.objects.create(
        user=wholesaler_user, company_name='Metro Distribution', status='active'
    )

    supplier_user, supplier = make_supplier()

    shop_a_user, shop_a = make_enterprise('shopa@test.com', 'Amma Stores',
                                          wholesaler=wholesaler)
    shop_b_user, shop_b = make_enterprise('shopb@test.com', 'Anna Nagar Organics',
                                          wholesaler=wholesaler)

    customer_a = make_customer('custa@test.com', shop_a)
    customer_b = make_customer('custb@test.com', shop_b)

    shared_product = make_shared_product(supplier, category)

    return SimpleNamespace(
        category=category,
        admin=admin,
        stockist=stockist, stockist_user=stockist_user,
        wholesaler=wholesaler, wholesaler_user=wholesaler_user,
        supplier=supplier, supplier_user=supplier_user,
        shop_a=shop_a, shop_a_user=shop_a_user,
        shop_b=shop_b, shop_b_user=shop_b_user,
        customer_a=customer_a, customer_b=customer_b,
        shared_product=shared_product,
        shared_variant=shared_product.variants.first(),
    )


def login_as(client, user):
    """Log in and pin the session's active role, the way a real login does."""
    client.force_login(user)
    session = client.session
    session['active_role'] = user.primary_role()
    session.save()
    return client
