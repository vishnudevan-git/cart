"""
Single-role policy — the rules that must never quietly regress.

Two invariants live here:

1. An account holds EXACTLY ONE role. Not zero, not two. Multi-role was removed
   deliberately: a shop that also wants to supply the marketplace opens a
   separate supplier account.
2. Personal shopping surfaces (profile, delivery addresses, rewards) belong to
   CUSTOMER accounts only. Admin is not a user — it administers accounts, it
   does not have a delivery address.

See PROJECT_NOTES.md "Stage 4".
"""
from django.test import TestCase
from django.urls import reverse, NoReverseMatch

from accounts.factories import build_world, make_user, login_as


class SingleRoleModelTests(TestCase):
    """The model layer must collapse any attempt to hold two roles."""

    def test_set_roles_keeps_only_the_first(self):
        user = make_user('multi@test.com', 'customer')
        user.set_roles(['distributor', 'supplier', 'customer'])
        user.save()
        user.refresh_from_db()

        self.assertEqual(user.get_roles_list(), ['distributor'])
        self.assertEqual(user.role, 'distributor')
        self.assertEqual(user.roles, 'distributor')

    def test_add_role_replaces_rather_than_appends(self):
        """`add_role` is a legacy name — under single-role it SETS the role."""
        user = make_user('swap@test.com', 'customer')
        user.add_role('supplier')
        user.save()
        user.refresh_from_db()

        self.assertEqual(user.get_roles_list(), ['supplier'])
        self.assertFalse(user.has_role('customer'))

    def test_has_multiple_roles_is_always_false(self):
        user = make_user('one@test.com', 'stockist')
        user.set_roles(['stockist', 'admin'])
        user.save()

        self.assertFalse(user.has_multiple_roles())

    def test_empty_or_unknown_roles_fall_back_to_customer(self):
        """An account can never end up with zero roles."""
        user = make_user('fallback@test.com', 'customer')

        user.set_roles([])
        self.assertEqual(user.get_roles_list(), ['customer'])

        user.set_roles(['not-a-real-role'])
        self.assertEqual(user.get_roles_list(), ['customer'])

    def test_save_keeps_role_and_roles_columns_in_sync(self):
        """`role` (legacy column) and `roles` (CSV) must never disagree."""
        user = make_user('sync@test.com', 'wholesaler')
        user.roles = 'supplier'          # write only one side...
        user.save()                      # ...save must reconcile them
        user.refresh_from_db()

        self.assertEqual(user.role, user.roles)
        self.assertEqual(user.primary_role(), 'supplier')

    def test_every_seeded_account_holds_exactly_one_role(self):
        build_world()
        from accounts.models import User

        for user in User.all_objects.all():
            with self.subTest(user=user.username):
                self.assertEqual(len(user.get_roles_list()), 1)
                self.assertEqual(user.role, user.primary_role())


class RoleSwitchingRemovedTests(TestCase):
    """There is no role switcher any more — not in URLs, not in templates."""

    def test_switch_role_url_no_longer_exists(self):
        with self.assertRaises(NoReverseMatch):
            reverse('accounts:switch_role')

    def test_pick_role_is_a_redirect_only_shim(self):
        """Old bookmarks must land on the user's own home, not 404."""
        world = build_world()
        client = login_as(self.client, world.shop_a_user)

        response = client.get(reverse('accounts:pick_role'))

        self.assertEqual(response.status_code, 302)
        self.assertNotIn('pick-role', response['Location'])

    def test_dashboard_has_no_role_switcher(self):
        world = build_world()
        client = login_as(self.client, world.admin)

        body = client.get(reverse('admin_panel:dashboard')).content.decode()

        self.assertNotIn('Switch role', body)
        self.assertNotIn('switch_role', body)


class CustomerOnlySurfaceTests(TestCase):
    """Profile / addresses / rewards are for customers. Admin is not a user."""

    CUSTOMER_URLS = ('accounts:profile', 'accounts:addresses', 'accounts:my_rewards')

    def setUp(self):
        self.world = build_world()

    def _business_users(self):
        return {
            'admin': self.world.admin,
            'supplier': self.world.supplier_user,
            'stockist': self.world.stockist_user,
            'wholesaler': self.world.wholesaler_user,
            'distributor': self.world.shop_a_user,
        }

    def test_business_roles_are_redirected_away(self):
        for role, user in self._business_users().items():
            for url_name in self.CUSTOMER_URLS:
                with self.subTest(role=role, url=url_name):
                    client = login_as(self.client_class(), user)
                    response = client.get(reverse(url_name))

                    self.assertEqual(
                        response.status_code, 302,
                        f'{role} should not reach {url_name}',
                    )

    def test_customer_can_still_reach_them(self):
        client = login_as(self.client, self.world.customer_a)

        for url_name in self.CUSTOMER_URLS:
            with self.subTest(url=url_name):
                self.assertEqual(client.get(reverse(url_name)).status_code, 200)

    def test_delete_address_is_customer_only(self):
        client = login_as(self.client, self.world.admin)

        response = client.post(reverse('accounts:delete_address', args=[1]))

        self.assertEqual(response.status_code, 302)

    def test_business_dashboards_show_no_personal_profile_link(self):
        """The profile icon is a shopping surface — dashboards must not offer it."""
        profile_url = reverse('accounts:profile')
        pages = {
            'admin': ('admin_panel:dashboard', self.world.admin),
            'supplier': ('suppliers:dashboard', self.world.supplier_user),
        }

        for role, (url_name, user) in pages.items():
            with self.subTest(role=role):
                client = login_as(self.client_class(), user)
                body = client.get(reverse(url_name)).content.decode()

                self.assertNotIn(f'href="{profile_url}"', body)


class TemplateHygieneTests(TestCase):
    """Django's `{# #}` is single-line only — a multi-line one leaks to the page."""

    def test_no_raw_template_comment_markers_render(self):
        world = build_world()
        pages = (
            ('admin_panel:dashboard', world.admin),
            ('suppliers:dashboard', world.supplier_user),
            ('distributors:own_product_list', world.shop_a_user),
        )

        for url_name, user in pages:
            with self.subTest(page=url_name):
                client = login_as(self.client_class(), user)
                body = client.get(reverse(url_name), follow=True).content.decode()

                self.assertNotIn('{#', body)
                self.assertNotIn('#}', body)
