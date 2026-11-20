"""Bulk actions on the admin Customers page (/admin-panel/customers/)."""
from django.test import TestCase
from django.urls import reverse

from accounts.factories import build_world, login_as
from accounts.models import User


class CustomerBulkActionTests(TestCase):
    def setUp(self):
        self.world = build_world()
        self.admin_client = login_as(self.client, self.world.admin)
        self.a = self.world.customer_a
        self.b = self.world.customer_b
        self.url = reverse('admin_panel:customer_bulk_action')

    def post(self, action, ids, **extra):
        data = {'action': action, 'customer_ids': [str(i) for i in ids], 'return_query': ''}
        data.update(extra)
        return self.admin_client.post(self.url, data)

    def test_page_renders_checkboxes_and_bulk_form(self):
        page = self.admin_client.get(reverse('admin_panel:customer_list'))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'id="bulkForm"')
        self.assertContains(page, f'value="{self.a.pk}"')
        self.assertContains(page, 'name="customer_ids"')

    def test_disable_then_enable_many(self):
        self.post('disable', [self.a.pk, self.b.pk])
        self.a.refresh_from_db(); self.b.refresh_from_db()
        self.assertFalse(self.a.is_active)
        self.assertFalse(self.b.is_active)
        self.post('enable', [self.a.pk, self.b.pk])
        self.a.refresh_from_db(); self.b.refresh_from_db()
        self.assertTrue(self.a.is_active)
        self.assertTrue(self.b.is_active)

    def test_bulk_delete_then_restore(self):
        self.post('delete', [self.a.pk, self.b.pk])
        self.a.refresh_from_db(); self.b.refresh_from_db()
        self.assertTrue(self.a.is_deleted and self.b.is_deleted)
        self.post('restore', [self.a.pk, self.b.pk])
        self.a.refresh_from_db(); self.b.refresh_from_db()
        self.assertFalse(self.a.is_deleted or self.b.is_deleted)
        self.assertTrue(self.a.is_active)

    def test_purge_only_affects_deleted_customers(self):
        self.post('delete', [self.a.pk])
        self.post('purge', [self.a.pk, self.b.pk])
        self.a.refresh_from_db(); self.b.refresh_from_db()
        self.assertTrue(self.a.is_purged)
        self.assertFalse(self.b.is_purged, 'an active customer was permanently archived')
        self.assertFalse(self.b.is_deleted)

    def test_assign_marketplace_to_many(self):
        self.post('assign_distributor', [self.a.pk, self.b.pk],
                  distributor_id=str(self.world.shop_b.pk))
        self.a.refresh_from_db(); self.b.refresh_from_db()
        self.assertEqual(self.a.distributor_id, self.world.shop_b.pk)
        self.assertEqual(self.b.distributor_id, self.world.shop_b.pk)

    def test_assign_without_a_marketplace_is_refused(self):
        before = self.a.distributor_id
        self.post('assign_distributor', [self.a.pk], distributor_id='')
        self.a.refresh_from_db()
        self.assertEqual(self.a.distributor_id, before)

    def test_admin_and_non_customers_are_never_changed(self):
        shop_user = self.world.shop_a_user
        admin = self.world.admin
        self.post('disable', [shop_user.pk, admin.pk])
        shop_user.refresh_from_db(); admin.refresh_from_db()
        self.assertTrue(shop_user.is_active)
        self.assertTrue(admin.is_active)

    def test_duplicate_ids_are_counted_once(self):
        self.post('disable', [self.a.pk, self.a.pk])
        self.a.refresh_from_db()
        self.assertFalse(self.a.is_active)

    def test_empty_selection_changes_nothing(self):
        self.post('disable', [])
        self.a.refresh_from_db()
        self.assertTrue(self.a.is_active)

    def test_unknown_action_is_refused(self):
        self.post('explode', [self.a.pk])
        self.a.refresh_from_db()
        self.assertTrue(self.a.is_active)
        self.assertFalse(self.a.is_deleted)

    def test_get_is_not_allowed(self):
        self.assertEqual(self.admin_client.get(self.url).status_code, 405)

    def test_inline_marketplace_dropdowns_confirm_before_submit(self):
        # Customers with no marketplace get an inline "Assign" dropdown. It must
        # not submit on change, or the confirm prompt is bypassed.
        self.a.distributor = None
        self.a.save(update_fields=['distributor'])
        page = self.admin_client.get(reverse('admin_panel:customer_list')).content.decode()
        import re
        assign_tags = re.findall(r'<select name="distributor_id"[^>]*>', page)
        self.assertGreater(len(assign_tags), 0)
        row_tags = [t for t in assign_tags if 'form="bulkForm"' not in t]   # per-row dropdowns
        self.assertGreater(len(row_tags), 0)
        for tag in row_tags:
            self.assertNotIn('onchange', tag, 'a row assign dropdown submits without confirming')
            self.assertIn('data-inline-assign="true"', tag)
        # The bulk toolbar dropdown is confirmed by its Assign button instead.
        bulk_tags = [t for t in assign_tags if 'form="bulkForm"' in t]
        self.assertEqual(len(bulk_tags), 1)
        self.assertNotIn('onchange', bulk_tags[0])

    def test_chain_above_marketplace_is_shown(self):
        self.world.wholesaler.stockist = self.world.stockist
        self.world.wholesaler.save(update_fields=['stockist'])
        page = self.admin_client.get(reverse('admin_panel:customer_list')).content.decode()
        self.assertIn('Distributor: Metro Distribution', page)
        self.assertIn('Stockist: City Stockist', page)
        self.assertIn('data-distributor="Metro Distribution" data-stockist="City Stockist"', page)

    def test_missing_chain_lines_are_hidden_not_shown_as_none(self):
        shop = self.world.shop_a
        shop.wholesaler = None
        shop.save(update_fields=['wholesaler'])
        page = self.admin_client.get(reverse('admin_panel:customer_list')).content.decode()
        self.assertNotIn('Distributor: none', page)
        self.assertNotIn('Stockist: none', page)

    def test_bulk_action_is_written_to_the_activity_log(self):
        with self.assertLogs('activity', level='INFO') as cm:
            self.post('disable', [self.a.pk, self.b.pk])
        text = '\n'.join(cm.output)
        self.assertIn('CUSTOMER_BULK | RECEIVED | action=disable | selected=2', text)
        self.assertIn('CUSTOMER_BULK | DONE | action=disable | selected=2 | applied=2 | skipped=0', text)

    def test_empty_selection_is_logged(self):
        with self.assertLogs('activity', level='INFO') as cm:
            self.post('disable', [])
        self.assertIn('CUSTOMER_BULK | NO_SELECTION | action=disable', '\n'.join(cm.output))

    def test_request_log_shows_posted_fields_but_never_secrets(self):
        with self.assertLogs('requests', level='INFO') as cm:
            self.admin_client.post(self.url, {
                'action': 'disable', 'customer_ids': [str(self.a.pk)],
                'password': 'SUPER-SECRET-123', 'address': 'Door 7, Street 9',
            })
        text = '\n'.join(cm.output)
        self.assertIn('action=disable', text)
        self.assertIn(f'customer_ids={self.a.pk}', text)
        self.assertIn('password=***', text)
        self.assertNotIn('SUPER-SECRET-123', text)
        self.assertNotIn('Door 7', text)
        self.assertIn('address=<1 value(s), not logged>', text)

    def test_page_leaves_selection_to_its_own_script(self):
        # datatable.js (shared) must not manage this page's selection or confirms.
        page = self.admin_client.get(reverse('admin_panel:customer_list')).content.decode()
        self.assertIn('window._bulkScriptOwner = true', page)
        import re
        for tag in re.findall(r'<button[^>]*bulk-action-btn[^>]*>', page):
            self.assertNotIn(' data-confirm="', tag, 'shared handler would also confirm this button')

    def test_non_admin_cannot_use_it(self):
        c = login_as(self.client_class(), self.world.shop_a_user)
        c.post(self.url, {'action': 'disable', 'customer_ids': [str(self.a.pk)]})
        self.a.refresh_from_db()
        self.assertTrue(self.a.is_active)
