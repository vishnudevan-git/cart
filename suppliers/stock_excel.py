"""Bulk stock update through an Excel sheet for the supplier stock page.

Three kinds of pack are shown:
- Plain packs: the supplier types the new pack count (yellow).
- Loose packs: each pool (one per variant type) has one yellow cell for the new
  pool, in kg, litres or pieces. Pack counts are formulas that recalculate from
  that pool, the same way the stock page does.
- Bundle packs: no typed stock. "Bundles available" is a formula.

Only yellow cells are applied on upload. Grey cells are information or formulas.

Import applies a typed value only when it differs from the value downloaded, and
only when the database still holds that downloaded value. Otherwise the row is
reported as changed since download, so an order placed in between is not
overwritten.
"""
from decimal import Decimal, InvalidOperation, ROUND_DOWN

HEADERS = [
    'Variant ID',
    'Product',
    'SKU',
    'Category',
    'Pack',
    'Pack size (g / ml / pcs)',
    'Box size (packs per bundle)',
    'Stock when downloaded (packs)',
    'Blocked by stockist requests (packs)',
    'New stock (packs)',
    'Loose pool when downloaded (kg / L / pcs)',
    'New loose pool (kg / L / pcs)',
    'Bundles available',
    'Note',
]
MAX_STOCK = 1_000_000
MAX_POOL_BASE = 100_000_000
LOOSE_FIRST_NOTE = 'Loose pool: type the new pool in the yellow column. Pack counts recalculate.'
LOOSE_SAME_NOTE = 'Same pool as the row above. Pack counts recalculate from it.'
LOOSE_NO_POOL_NOTE = 'Loose product without a pool: change its stock on the stock page'
BUNDLE_NOTE = 'Bundle pack: no entry here. Set its stock on the product edit page'
EDIT_FILL = 'FFF2CC'      # light yellow: type here
INFO_FILL = 'EDEDED'      # light grey: information or formula only
HEADER_FILL = 'D9D9D9'

COL = dict(zip(
    ['id', 'product', 'sku', 'category', 'pack', 'unit', 'box', 'down', 'blocked', 'new',
     'pool_down', 'pool_new', 'bundles', 'note'],
    'ABCDEFGHIJKLMN'))


def _owned_variants(supplier):
    from products.models import ProductVariant
    return (ProductVariant.objects.filter(product__supplier=supplier, product__is_deleted=False, is_active=True)
            .select_related('product', 'product__category', 'variant_type')
            .order_by('product__name', 'variant_type__position', 'selling_price', 'id'))


def _box_sizes(supplier):
    from products.models import ProductBundle
    return {b.variant_id: b.pack_quantity
            for b in ProductBundle.objects.filter(product__supplier=supplier,
                                                 product__is_deleted=False, is_active=True)}


def _kind(variant, boxes):
    """'loose', 'bundle' or 'plain'. Loose wins when a pack is both."""
    from suppliers.views import _is_pool_product
    if _is_pool_product(variant.product):
        return 'loose'
    if boxes.get(variant.pk):
        return 'bundle'
    return 'plain'


def _pool_factor(product):
    """Base units per display unit: 1000 for kg and litres, 1 for pieces."""
    from suppliers.views import _POOL_UNIT
    return _POOL_UNIT.get(product.master_unit, ('units', 1))[1]


def build_stock_workbook(supplier):
    """Return an openpyxl Workbook: a coloured 'Stock' sheet and a 'Read me' sheet."""
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill, Protection

    variants = list(_owned_variants(supplier))
    boxes = _box_sizes(supplier)
    kinds = {v.pk: _kind(v, boxes) for v in variants}

    # Sheet row of the first pack of each loose pool. That row holds the yellow pool cell.
    pool_row = {}
    for index, v in enumerate(variants):
        if kinds[v.pk] == 'loose' and v.variant_type_id and v.variant_type_id not in pool_row:
            pool_row[v.variant_type_id] = index + 2          # data starts on row 2

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Stock'
    ws.append(HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill('solid', fgColor=HEADER_FILL)
        cell.alignment = Alignment(wrap_text=True, vertical='center')
    ws.row_dimensions[1].height = 48

    yellow = PatternFill('solid', fgColor=EDIT_FILL)
    grey = PatternFill('solid', fgColor=INFO_FILL)
    for index, v in enumerate(variants):
        r = index + 2
        kind = kinds[v.pk]
        box = boxes.get(v.pk)
        factor = _pool_factor(v.product)
        values = {c: None for c in COL.values()}
        values.update({
            COL['id']: v.pk,
            COL['product']: v.product.name,
            COL['sku']: v.product.sku,
            COL['category']: v.product.category.name if v.product.category else '',
            COL['pack']: v.label,
            COL['unit']: v.unit_grams or None,
            COL['box']: box,
            COL['down']: v.stock,
            COL['blocked']: v.blocked_stock,
            COL['bundles']: (f'=IF({COL["box"]}{r}="","",'
                             f'INT({COL["down"]}{r}/{COL["box"]}{r}))'),
        })
        yellow_cells = []
        if kind == 'plain':
            values[COL['new']] = v.stock
            yellow_cells.append(COL['new'])
        elif kind == 'loose' and v.variant_type_id:
            first = pool_row[v.variant_type_id]
            display = round((v.variant_type.master_stock_grams or 0) / factor, 3)
            values[COL['pool_down']] = display
            if r == first:
                values[COL['pool_new']] = display
                values[COL['note']] = LOOSE_FIRST_NOTE
                yellow_cells.append(COL['pool_new'])
            else:
                values[COL['pool_new']] = f'={COL["pool_new"]}{first}'
                values[COL['note']] = LOOSE_SAME_NOTE
            pool_cell = f'{COL["pool_new"]}{first}'
            unit_cell = f'{COL["unit"]}{r}'
            values[COL['new']] = (
                f'=IF(OR({pool_cell}="",{unit_cell}=0),"",'
                f'INT(ROUND({pool_cell}*{factor},0)/{unit_cell}))')
        elif kind == 'loose':
            values[COL['note']] = LOOSE_NO_POOL_NOTE
        else:                                              # bundle
            values[COL['note']] = BUNDLE_NOTE
        ws.append([values[c] for c in COL.values()])

        for letter in COL.values():
            ws[f'{letter}{r}'].fill = grey
        for letter in yellow_cells:
            ws[f'{letter}{r}'].fill = yellow
            ws[f'{letter}{r}'].font = Font(bold=True)
            ws[f'{letter}{r}'].protection = Protection(locked=False)

    widths = {'A': 11, 'B': 30, 'C': 14, 'D': 18, 'E': 14, 'F': 14, 'G': 16,
              'H': 18, 'I': 18, 'J': 16, 'K': 22, 'L': 22, 'M': 16, 'N': 60}
    for col, width in widths.items():
        ws.column_dimensions[col].width = width
    ws.freeze_panes = 'A2'
    ws.protection.sheet = True          # locks grey cells; yellow cells stay editable

    _add_read_me(wb)
    wb.active = 0
    return wb


def _add_read_me(wb):
    from openpyxl.styles import Font, PatternFill

    sheet = wb.create_sheet('Read me')
    lines = [
        ('How to update stock', True, None),
        ('1. Go to the "Stock" sheet.', False, None),
        ('2. Plain packs: type the new total number of packs in the yellow column '
         '"New stock (packs)".', False, EDIT_FILL),
        ('3. Loose products: type the new pool (kg, litres or pieces) in the yellow '
         'column "New loose pool". Pack counts recalculate by themselves.', False, EDIT_FILL),
        ('4. Leave yellow cells empty for anything you are not changing.', False, None),
        ('5. Save the file and upload it on the stock page.', False, None),
        ('', False, None),
        ('What the colours mean', True, None),
        ('Yellow: type here. Only yellow cells are applied on upload.', False, EDIT_FILL),
        ('Grey: information or formula. Changes here are ignored.', False, INFO_FILL),
        ('', False, None),
        ('Column notes', True, None),
        ('Variant ID: the internal number of the pack. The upload uses it to match rows, so '
         'leave it as it is. You do not need to know its meaning.', False, None),
        ('SKU: the product code, shared by all packs of the same product. For reference only.',
         False, None),
        ('Box size (packs per bundle): set on the product edit page.', False, None),
        ('Stock when downloaded and Loose pool when downloaded: used to detect changes '
         'made after download. Do not change them.', False, None),
        ('Blocked by stockist requests: packs promised to open requests. They are still '
         'yours until shipped. Information only.', False, None),
        ('Bundles available: pack stock divided by box size. Calculated automatically.',
         False, None),
        ('', False, None),
        ('Bundle packs have no yellow cell. Their stock is set on the product edit page.',
         False, None),
        ('Do not add or delete rows. New packs cannot be added from this sheet.', False, None),
    ]
    for index, (text, bold, fill) in enumerate(lines, start=1):
        cell = sheet.cell(row=index, column=1, value=text)
        cell.font = Font(bold=bold)
        if fill:
            cell.fill = PatternFill('solid', fgColor=fill)
    sheet.column_dimensions['A'].width = 110
    sheet.protection.sheet = True


def _decimal(value):
    """Return ``value`` as a finite Decimal, or raise ValueError."""
    if isinstance(value, bool) or value is None:
        raise ValueError
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValueError
    if not number.is_finite():
        raise ValueError
    return number


def _whole_number(value):
    """Return ``value`` as an int, or raise ValueError if it is not a whole number."""
    if isinstance(value, bool):
        raise ValueError
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise ValueError


def _to_base(display, factor):
    """Display amount (kg, litres or pieces) to base units (grams, ml or pieces).

    Truncates like the stock page does (int of amount x factor).
    """
    return int((display * factor).to_integral_value(rounding=ROUND_DOWN))


def apply_stock_workbook(supplier, uploaded):
    """Apply an uploaded stock sheet. Returns (updated_count, skipped).

    ``skipped`` is a list of (label, reason) pairs. Raises ValueError when the
    file is not a readable stock sheet.
    """
    import openpyxl
    from products.models import ProductVariant, ProductVariantType

    try:
        wb = openpyxl.load_workbook(uploaded, data_only=True)
    except Exception:
        raise ValueError('The file could not be read. Upload the .xlsx sheet '
                         'downloaded from this page.')
    ws = wb.worksheets[0]
    rows = ws.iter_rows(values_only=True)
    header = [str(h or '').strip() for h in (next(rows, None) or ())]
    try:
        id_col = header.index('Variant ID')
        down_col = header.index('Stock when downloaded (packs)')
        new_col = header.index('New stock (packs)')
        pool_down_col = header.index('Loose pool when downloaded (kg / L / pcs)')
        pool_new_col = header.index('New loose pool (kg / L / pcs)')
    except ValueError:
        raise ValueError('Required columns are missing. Use the sheet downloaded '
                         'from this page.')

    def cell(row, index):
        return row[index] if index < len(row) else None

    boxes = _box_sizes(supplier)
    owned = {v.pk: v for v in _owned_variants(supplier)}
    updated = 0
    skipped = []
    pools = {}                    # variant_type id -> first row of that pool in the sheet
    for row_no, row in enumerate(rows, start=2):
        if row is None or all(c in (None, '') for c in row):
            continue
        try:
            vid = _whole_number(cell(row, id_col))
        except ValueError:
            skipped.append((f'row {row_no}', 'Variant ID is missing or invalid'))
            continue
        variant = owned.get(vid)
        if variant is None:
            skipped.append((f'row {row_no}', 'not one of your packs'))
            continue
        label = f'{variant.product.name} ({variant.label})'
        kind = _kind(variant, boxes)

        if kind == 'loose':
            if variant.variant_type_id and variant.variant_type_id not in pools:
                pools[variant.variant_type_id] = (
                    row_no, variant, cell(row, pool_down_col), cell(row, pool_new_col))
            continue                # pack counts follow the pool; handled below

        raw_new = cell(row, new_col)
        if raw_new in (None, ''):
            continue                # nothing typed in this row
        if kind == 'bundle':
            skipped.append((label, BUNDLE_NOTE))
            continue
        try:
            new = _whole_number(raw_new)
        except ValueError:
            skipped.append((label, 'not a whole number'))
            continue
        if new < 0:
            skipped.append((label, 'cannot be negative'))
            continue
        if new > MAX_STOCK:
            skipped.append((label, f'exceeds the {MAX_STOCK:,} limit'))
            continue
        try:
            old = _whole_number(cell(row, down_col))
        except ValueError:
            skipped.append((label, 'stock when downloaded is missing'))
            continue
        if new == old:
            continue
        saved = ProductVariant.objects.filter(pk=vid, stock=old).update(stock=new)
        if saved:
            updated += 1
        else:
            skipped.append((label, 'changed since download, so not saved. '
                                   'Download the sheet again'))

    for vt_id, (row_no, variant, raw_down, raw_new) in pools.items():
        label = f'{variant.product.name} (loose pool)'
        if raw_new in (None, ''):
            continue                # pool cell left empty: no change
        factor = _pool_factor(variant.product)
        try:
            new_display = _decimal(raw_new)
        except ValueError:
            skipped.append((label, 'pool is not a number'))
            continue
        if new_display < 0:
            skipped.append((label, 'pool cannot be negative'))
            continue
        try:
            old_display = _decimal(raw_down)
        except ValueError:
            skipped.append((label, 'pool when downloaded is missing'))
            continue
        new_base = _to_base(new_display, factor)
        old_base = _to_base(old_display, factor)
        if new_base > MAX_POOL_BASE:
            skipped.append((label, 'pool is unrealistically large'))
            continue
        if new_base == old_base:
            continue
        saved = ProductVariantType.objects.filter(
            pk=vt_id, master_stock_grams=old_base).update(master_stock_grams=new_base)
        if saved:
            ProductVariantType.objects.get(pk=vt_id).sync_pack_stocks()
            updated += 1
        else:
            skipped.append((label, 'changed since download, so not saved. '
                                   'Download the sheet again'))
    return updated, skipped
