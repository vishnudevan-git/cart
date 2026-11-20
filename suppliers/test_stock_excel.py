"""Bulk stock update through an Excel sheet."""
from io import BytesIO

import openpyxl
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from accounts.factories import build_world, login_as, make_category, make_shared_product, make_supplier
from products.models import ProductBundle, ProductVariant, ProductVariantType
from suppliers.stock_excel import EDIT_FILL, HEADERS

# Column positions in the Stock sheet
ID, SKU, PACK, UNIT, BOX, DOWN, BLOCKED, NEW, POOL_DOWN, POOL_NEW, BUNDLES = 0, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12


def _upload_to(client, wb, name='stock.xlsx'):
    buf = BytesIO()
    wb.save(buf)
    upload = SimpleUploadedFile(name, buf.getvalue(), content_type='application/octet-stream')
    return client.post(reverse('suppliers:stock_import'), {'stock_file': upload}, follow=True)


def _download(client):
    response = client.get(reverse('suppliers:stock_export'))
    assert response.status_code == 200
    return openpyxl.load_workbook(BytesIO(response.content))


def _row_for(wb, vid):
    for row in wb['Stock'].iter_rows(min_row=2):
        if row[ID].value == vid:
            return row
    raise AssertionError(f'variant {vid} not in sheet')


class StockSheetTests(TestCase):
    def setUp(self):
        self.world = build_world()
        self.client_s = login_as(self.client, self.world.supplier_user)
        product = self.world.shared_product
        # Plain pack: no bundle, typed stock.
        self.variant = ProductVariant.objects.create(
            product=product, label='1 kg', selling_price='900', stock=100, unit_grams=1000)
        # Bundle pack: stock is not typed in the sheet.
        self.bundle_variant = self.world.shared_variant      # 500 g, stock 100
        self.bundle_variant.stock = 100
        self.bundle_variant.save(update_fields=['stock'])
        ProductBundle.objects.create(
            product=product, variant=self.bundle_variant,
            name='Box of 10', pack_quantity=10, is_active=True)

    def _download(self):
        return _download(self.client_s)

    def _upload(self, wb, name='stock.xlsx'):
        return _upload_to(self.client_s, wb, name)

    def test_sheet_has_stock_and_read_me_tabs(self):
        wb = self._download()
        self.assertEqual(wb.sheetnames, ['Stock', 'Read me'])
        self.assertEqual([c.value for c in wb['Stock'][1]], HEADERS)

    def test_plain_pack_row_is_editable_and_highlighted(self):
        row = _row_for(self._download(), self.variant.pk)
        self.assertEqual(row[SKU].value, self.world.shared_product.sku)
        self.assertEqual(row[DOWN].value, 100)          # stock when downloaded
        self.assertEqual(row[NEW].value, 100)           # New stock, pre-filled
        self.assertEqual(row[NEW].fill.fgColor.rgb[-6:], EDIT_FILL)
        self.assertFalse(row[NEW].protection.locked)

    def test_bundle_row_has_formula_and_no_entry(self):
        row = _row_for(self._download(), self.bundle_variant.pk)
        r = row[0].row
        self.assertEqual(row[BOX].value, 10)
        self.assertEqual(row[BUNDLES].value, f'=IF(G{r}="","",INT(H{r}/G{r}))')
        self.assertIsNone(row[NEW].value)
        self.assertTrue(row[NEW].protection.locked)

    def test_deleted_products_are_left_out_of_sheet_and_stock_page(self):
        from products.models import Product
        Product.all_objects.filter(pk=self.world.shared_product.pk).update(is_deleted=True)
        ids = [r[ID].value for r in self._download()['Stock'].iter_rows(min_row=2)]
        self.assertNotIn(self.variant.pk, ids)
        self.assertNotIn(self.bundle_variant.pk, ids)
        body = self.client_s.get(reverse('suppliers:stock_manage')).content.decode()
        self.assertNotIn('Marketplace Cashew', body)

    def test_download_leaves_out_other_suppliers_packs(self):
        _, other = make_supplier(username='other@test.com', company='Other Farms')
        foreign = make_shared_product(other, make_category(name='Nuts', slug='nuts', code='NU'),
                                      name='Other Nuts', stock=7)
        ids = [r[ID].value for r in self._download()['Stock'].iter_rows(min_row=2)]
        self.assertNotIn(foreign.variants.first().pk, ids)

    def test_upload_updates_plain_pack(self):
        wb = self._download()
        _row_for(wb, self.variant.pk)[NEW].value = 130
        self._upload(wb)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 130)

    def test_typed_value_on_bundle_row_is_refused(self):
        wb = self._download()
        _row_for(wb, self.bundle_variant.pk)[NEW].value = 50
        response = self._upload(wb)
        self.bundle_variant.refresh_from_db()
        self.assertEqual(self.bundle_variant.stock, 100)
        self.assertContains(response, 'Bundle pack: no entry here')

    def test_unchanged_sheet_changes_nothing(self):
        response = self._upload(self._download())
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 100)
        self.assertContains(response, 'no stock changes')

    def test_stock_changed_after_download_is_not_overwritten(self):
        wb = self._download()                                  # downloaded at 100
        ProductVariant.objects.filter(pk=self.variant.pk).update(stock=80)   # order arrives
        _row_for(wb, self.variant.pk)[NEW].value = 50
        response = self._upload(wb)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 80)
        self.assertContains(response, 'changed since download')

    def test_negative_and_fractional_values_are_rejected(self):
        wb = self._download()
        _row_for(wb, self.variant.pk)[NEW].value = -3
        response = self._upload(wb)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 100)
        self.assertContains(response, 'cannot be negative')

        wb = self._download()
        _row_for(wb, self.variant.pk)[NEW].value = '12.5'
        response = self._upload(wb)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 100)
        self.assertContains(response, 'not a whole number')

    def test_row_for_another_suppliers_pack_is_refused(self):
        _, other = make_supplier(username='other@test.com', company='Other Farms')
        foreign = make_shared_product(other, make_category(name='Nuts', slug='nuts', code='NU'),
                                      name='Other Nuts', stock=7).variants.first()
        wb = openpyxl.Workbook()
        wb.active.append(HEADERS)
        wb.active.append([foreign.pk, 'Other Nuts', '', '', foreign.label, None, None,
                          7, 50, None, None, None, None])
        response = self._upload(wb)
        foreign.refresh_from_db()
        self.assertEqual(foreign.stock, 7)
        self.assertContains(response, 'not one of your packs')

    def test_wrong_sheet_is_refused(self):
        wb = openpyxl.Workbook()
        wb.active.append(['Name', 'Qty'])
        wb.active.append(['x', 1])
        response = self._upload(wb)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 100)
        self.assertContains(response, 'Required columns are missing')

    def test_non_excel_file_is_refused(self):
        response = self.client_s.post(reverse('suppliers:stock_import'), {
            'stock_file': SimpleUploadedFile('stock.csv', b'a,b', content_type='text/csv'),
        }, follow=True)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 100)
        self.assertContains(response, 'Upload an .xlsx file')


class LooseSheetTests(TestCase):
    """Loose pool: one yellow pool cell per pool; pack counts follow it."""

    def setUp(self):
        self.world = build_world()
        self.client_s = login_as(self.client, self.world.supplier_user)
        product = make_shared_product(self.world.supplier, self.world.category,
                                      name='Cashews', stock=0)
        product.allow_loose_distribution = True
        product.master_unit = 'g'
        product.save()
        self.pool = ProductVariantType.objects.create(
            product=product, name='', master_stock_grams=123000)   # 123 kg
        self.pack100 = product.variants.first()
        ProductVariant.objects.filter(pk=self.pack100.pk).update(
            variant_type=self.pool, label='100g', unit_grams=100, stock=1230)
        self.pack250 = ProductVariant.objects.create(
            product=product, variant_type=self.pool, label='250g', selling_price='400',
            unit_grams=250, stock=492)
        ProductVariant.objects.filter(pk=self.pack250.pk).update(stock=492)
        self.pack100.refresh_from_db()

    def _pool_rows(self, wb):
        rows = [r for r in wb['Stock'].iter_rows(min_row=2)
                if r[ID].value in (self.pack100.pk, self.pack250.pk)]
        first = next(r for r in rows if not str(r[POOL_NEW].value or '').startswith('='))
        other = next(r for r in rows if r is not first)
        return first, other

    def test_pool_is_shown_once_as_yellow_and_packs_are_formulas(self):
        wb = self._download()
        first, other = self._pool_rows(wb)
        self.assertEqual(first[POOL_DOWN].value, 123)
        self.assertEqual(first[POOL_NEW].value, 123)
        self.assertEqual(first[POOL_NEW].fill.fgColor.rgb[-6:], EDIT_FILL)
        self.assertEqual(other[POOL_NEW].value, f'=L{first[0].row}')
        self.assertIn(f'ROUND(L{first[0].row}*1000,0)', first[NEW].value)
        self.assertIn('ROUND(L', other[NEW].value)

    def test_changing_pool_updates_pool_and_pack_stock(self):
        wb = self._download()
        first, _ = self._pool_rows(wb)
        first[POOL_NEW].value = 100                       # 100 kg
        self._upload(wb)
        self.pool.refresh_from_db()
        self.pack100.refresh_from_db()
        self.pack250.refresh_from_db()
        self.assertEqual(self.pool.master_stock_grams, 100000)
        self.assertEqual(self.pack100.stock, 1000)        # 100000 // 100
        self.assertEqual(self.pack250.stock, 400)         # 100000 // 250

    def test_unchanged_pool_changes_nothing(self):
        response = self._upload(self._download())
        self.pool.refresh_from_db()
        self.assertEqual(self.pool.master_stock_grams, 123000)
        self.assertContains(response, 'no stock changes')

    def test_pool_changed_after_download_is_not_overwritten(self):
        wb = self._download()
        ProductVariantType.objects.filter(pk=self.pool.pk).update(master_stock_grams=50000)
        first, _ = self._pool_rows(wb)
        first[POOL_NEW].value = 100
        response = self._upload(wb)
        self.pool.refresh_from_db()
        self.assertEqual(self.pool.master_stock_grams, 50000)
        self.assertContains(response, 'changed since download')

    def test_negative_pool_is_rejected(self):
        wb = self._download()
        first, _ = self._pool_rows(wb)
        first[POOL_NEW].value = -5
        response = self._upload(wb)
        self.pool.refresh_from_db()
        self.assertEqual(self.pool.master_stock_grams, 123000)
        self.assertContains(response, 'pool cannot be negative')

    def test_typing_over_pack_count_on_loose_row_is_ignored(self):
        wb = self._download()
        _, other = self._pool_rows(wb)
        other[NEW].value = 5
        self._upload(wb)
        self.pack250.refresh_from_db()
        self.pool.refresh_from_db()
        self.assertEqual(self.pool.master_stock_grams, 123000)
        self.assertEqual(self.pack250.stock, 492)

    def _download(self):
        return _download(self.client_s)

    def _upload(self, wb):
        return _upload_to(self.client_s, wb)
