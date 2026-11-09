"""
Marketplace (shop) own-products — the privacy boundary.

A shop may list its OWN goods and sell them in its OWN store. Those products
are private to that one shop: no other shop, no supplier, no stockist, not the
marketplace and not the admin approval queue may see or source them. A shop
that wants to supply the wider marketplace opens a SEPARATE supplier account.

The boundary is enforced by the default manager — ``Product.objects`` hides
owner_enterprise rows, ``Product.all_objects`` is the deliberate escape hatch.
That design is only fail-safe as long as these tests keep passing: if someone
swaps a manager or forgets an ownership filter, one of them goes red.

See PROJECT_NOTES.md "Stage 4" and the single-role-policy notes.
"""
from django.test import TestCase
from django.urls import reverse

from accounts.factories import build_world, login_as
from cart.models import Cart
from products.models import Product
from suppliers.models import Supplier

from .models import DistributorProduct, DistributorVariantPrice


OWN_PRODUCT_NAME = 'Amma Home Pickle'


class OwnProductTestCase(TestCase):
    """Shared setup: shop A has published one own product."""

    def setUp(self):
        self.world = build_world()
        self.shop_a = self.world.shop_a
        self.shop_b = self.world.shop_b
        self.client_a = login_as(self.client_class(), self.world.shop_a_user)
        self.client_b = login_as(self.client_class(), self.world.shop_b_user)

    def create_own_product(self, client=None, name=OWN_PRODUCT_NAME, price='120',
                           label='250 g', stock='30'):
        response = (client or self.client_a).post(
            reverse('distributors:own_product_create'),
            {
                'name': name,
                'category': self.world.category.pk,
                'description': 'Made in the shop',
                'variant_id': [''],
                'variant_label': [label],
                'variant_price': [price],
                'variant_mrp': ['150'],
                'variant_stock': [stock],
                'variant_grams': ['250'],
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        product = Product.all_objects.filter(name=name).order_by('-pk').first()
        self.assertIsNotNone(product, f'own product "{name}" was not created')
        return product


class OwnProductCreationTests(OwnProductTestCase):

    def test_a_brand_new_shop_can_reach_its_own_products(self):
        """Own products bypass the marketplace pricing chain entirely, so the
        Global Pricing setup gate must not lock a new shop out of them."""
        response = self.client_a.get(reverse('distributors:own_product_list'))

        self.assertEqual(response.status_code, 200)

    def test_created_product_is_owned_and_immediately_live(self):
        product = self.create_own_product()

        self.assertEqual(product.owner_enterprise, self.shop_a)
        self.assertTrue(product.is_enterprise_owned)
        # No admin approval and no stockist charges stand between a shop's own
        # product and its own customers.
        self.assertEqual(product.status, 'approved')
        self.assertTrue(product.charges_confirmed)
        self.assertTrue(
            DistributorProduct.objects.filter(
                distributor=self.shop_a, product=product, is_listed=True
            ).exists()
        )

    def test_shop_price_is_the_customer_price(self):
        """No supply chain behind an own product — no margin may be stacked."""
        product = self.create_own_product(price='120')
        variant = product.variants.first()
        listing = DistributorVariantPrice.objects.get(
            distributor=self.shop_a, variant=variant
        )

        self.assertEqual(listing.selling_price, variant.selling_price)
        self.assertEqual(listing.base_price, variant.selling_price)

    def test_two_own_products_in_one_category_get_distinct_skus(self):
        """SKU sequencing must count shop-private products too, or the second
        one collides with the first on the unique constraint."""
        first = self.create_own_product(name='Amma Pickle One')
        second = self.create_own_product(name='Amma Pickle Two')

        self.assertNotEqual(first.sku, second.sku)

    def test_house_supplier_is_hidden_from_every_supplier_surface(self):
        """A shop's own products hang off a hidden in-house Supplier row because
        Product.supplier is schema-required. It is a data holder, never a role."""
        self.create_own_product()

        self.assertFalse(Supplier.objects.filter(user=self.world.shop_a_user).exists())
        house = Supplier.all_objects.get(user=self.world.shop_a_user)
        self.assertTrue(house.is_house_account)


class OwnProductIsolationTests(OwnProductTestCase):
    """Nobody outside the owning shop may see the product."""

    def setUp(self):
        super().setUp()
        self.product = self.create_own_product()

    # assertFalse on a boolean rather than assertNotIn on the body: a failing
    # assertNotIn dumps the entire rendered page into the test output.
    def assertHidden(self, response, who):
        self.assertFalse(
            OWN_PRODUCT_NAME in response.content.decode(),
            f'{who} can see another shop\'s private product',
        )

    def assertVisible(self, response, who):
        self.assertTrue(
            OWN_PRODUCT_NAME in response.content.decode(),
            f'{who} should be able to see this product',
        )

    def test_default_manager_excludes_it(self):
        """Every unaudited query in the codebase goes through Product.objects."""
        self.assertFalse(Product.objects.filter(pk=self.product.pk).exists())
        self.assertTrue(Product.all_objects.filter(pk=self.product.pk).exists())

    def test_visible_to_enterprise_scopes_by_owner(self):
        visible_a = Product.all_objects.visible_to_enterprise(self.shop_a)
        visible_b = Product.all_objects.visible_to_enterprise(self.shop_b)

        self.assertIn(self.product, visible_a)
        self.assertNotIn(self.product, visible_b)
        # The shared marketplace product stays visible to both.
        self.assertIn(self.world.shared_product, visible_a)
        self.assertIn(self.world.shared_product, visible_b)

    def test_owning_shop_sees_it_in_its_own_lists(self):
        self.assertVisible(
            self.client_a.get(reverse('distributors:own_product_list')), 'owning shop')
        self.assertVisible(
            self.client_a.get(reverse('distributors:product_catalogue')), 'owning shop')

    def test_rival_shop_sees_it_nowhere(self):
        self.assertHidden(
            self.client_b.get(reverse('distributors:own_product_list')), 'rival shop')
        self.assertHidden(
            self.client_b.get(reverse('distributors:product_catalogue')), 'rival shop')

    def test_upstream_roles_see_it_nowhere(self):
        """Every marketplace-side listing, not just the obvious one.

        Product.objects protects queries that start from Product. These pages
        start from ProductVariant instead, which is NOT filtered by default —
        they have to say .shared(). That is exactly the kind of thing that gets
        forgotten, so each surface is asserted individually.
        """
        pages = (
            ('admin product list', 'admin_panel:product_list', self.world.admin),
            ('admin stock', 'admin_panel:stock_adjust', self.world.admin),
            ('admin low stock', 'admin_panel:low_stock', self.world.admin),
            ('stockist inventory', 'stockist:inventory', self.world.stockist_user),
            ('supplier products', 'suppliers:product_list', self.world.supplier_user),
        )

        for who, url_name, user in pages:
            with self.subTest(page=who):
                client = login_as(self.client_class(), user)
                self.assertHidden(client.get(reverse(url_name), follow=True), who)

    def test_variant_shared_helper_excludes_private_packs(self):
        from products.models import ProductVariant

        private = self.product.variants.first()
        shared = self.world.shared_variant

        self.assertNotIn(private, ProductVariant.objects.shared())
        self.assertIn(shared, ProductVariant.objects.shared())
        # The owning shop still reaches its own packs through the relation.
        self.assertIn(private, self.product.variants.all())


class OwnProductShoppingTests(OwnProductTestCase):
    """The owning shop's customers can buy it; another shop's customers cannot."""

    def setUp(self):
        super().setUp()
        self.product = self.create_own_product()
        self.variant = self.product.variants.first()

    def test_owning_shops_customer_can_see_and_cart_it(self):
        client = login_as(self.client_class(), self.world.customer_a)

        listing = client.get(reverse('products:product_list'))
        self.assertIn(OWN_PRODUCT_NAME, listing.content.decode())

        client.post(reverse('cart:add', args=[self.variant.pk]), {'quantity': 1})
        self.assertTrue(
            Cart.objects.filter(customer=self.world.customer_a, variant=self.variant).exists()
        )

    def test_rival_shops_customer_can_neither_see_nor_cart_it(self):
        client = login_as(self.client_class(), self.world.customer_b)

        listing = client.get(reverse('products:product_list'))
        self.assertNotIn(OWN_PRODUCT_NAME, listing.content.decode())

        client.post(reverse('cart:add', args=[self.variant.pk]), {'quantity': 1})
        self.assertFalse(
            Cart.objects.filter(customer=self.world.customer_b, variant=self.variant).exists(),
            'a customer of another shop was able to cart a private product',
        )


class OwnProductWriteProtectionTests(OwnProductTestCase):
    """A shop may only ever write to its own products."""

    def setUp(self):
        super().setUp()
        self.product = self.create_own_product()

    def test_rival_shop_cannot_edit_toggle_or_delete(self):
        attempts = (
            ('edit', 'get', 'distributors:own_product_edit'),
            ('toggle', 'post', 'distributors:own_product_toggle'),
            ('delete', 'post', 'distributors:own_product_delete'),
        )

        for what, method, url_name in attempts:
            with self.subTest(action=what):
                url = reverse(url_name, args=[self.product.pk])
                response = getattr(self.client_b, method)(url)

                self.assertEqual(response.status_code, 404, f'rival shop could {what}')

    def test_rival_shop_cannot_delete_by_side_effect(self):
        self.client_b.post(reverse('distributors:own_product_delete', args=[self.product.pk]))
        self.product.refresh_from_db()

        self.assertFalse(self.product.is_deleted)

    def test_owner_delete_is_soft_and_unlists(self):
        """No hard deletes anywhere in this project — the row is retained."""
        self.client_a.post(reverse('distributors:own_product_delete', args=[self.product.pk]))
        self.product.refresh_from_db()

        self.assertTrue(Product.all_objects.filter(pk=self.product.pk).exists())
        self.assertTrue(self.product.is_deleted)
        self.assertIsNotNone(self.product.deleted_at)
        self.assertFalse(
            DistributorProduct.objects.get(
                distributor=self.shop_a, product=self.product
            ).is_listed
        )

    def test_owner_toggle_hides_without_deleting(self):
        self.client_a.post(reverse('distributors:own_product_toggle', args=[self.product.pk]))

        self.assertFalse(
            DistributorProduct.objects.get(
                distributor=self.shop_a, product=self.product
            ).is_listed
        )
        self.product.refresh_from_db()
        self.assertFalse(self.product.is_deleted)
