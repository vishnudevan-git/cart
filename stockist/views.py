from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db.models import Sum, Q
from functools import wraps
from accounts.decorators import ensure_user_role
from payments.models import sync_order_ledger
from .models import Stockist, StockistInventory, ProcessingCharge, StockistRestockRequest, StockistStage
from products.models import Product, ProductVariant, Category


def stockist_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        denial = ensure_user_role(
            request,
            ('stockist',),
            set_active_role='stockist',
        )
        if denial:
            return denial
        try:
            stockist = request.user.stockist_profile
            if stockist.status != 'active':
                return render(request, 'stockist/suspended.html', {'stockist': stockist})
        except Stockist.DoesNotExist:
            return redirect('accounts:login')
        return view_func(request, *args, **kwargs)
    return wrapper


@stockist_required
def dashboard(request):
    stockist = request.user.stockist_profile
    total_variants = StockistInventory.objects.filter(stockist=stockist).count()
    in_stock = StockistInventory.objects.filter(stockist=stockist, quantity__gt=0).count()
    low_stock = StockistInventory.objects.filter(stockist=stockist, quantity__gt=0, quantity__lte=10).count()
    pending_restock = StockistRestockRequest.objects.filter(stockist=stockist, status='pending').count()
    pending_release = Product.objects.filter(status='approved', charges_confirmed=False, is_deleted=False).count()
    recent_inventory = StockistInventory.objects.filter(
        stockist=stockist
    ).select_related('variant__product').order_by('-updated_at')[:10]
    return render(request, 'stockist/dashboard.html', {
        'stockist': stockist,
        'total_variants': total_variants,
        'in_stock': in_stock,
        'low_stock': low_stock,
        'pending_restock': pending_restock,
        'pending_release': pending_release,
        'recent_inventory': recent_inventory,
    })


@stockist_required
def inventory(request):
    """Stockist manages their buffer stock."""
    stockist = request.user.stockist_profile
    category_filter = request.GET.get('category', '')
    supplier_filter = request.GET.get('supplier', '')

    # .shared(): a shop's own products never enter the supply chain, so the
    # stockist neither stocks nor prices them. Direct-supply products go
    # supplier → marketplace and never pass through the stockist either.
    variants = ProductVariant.objects.shared().filter(
        is_active=True, product__status='approved', product__is_deleted=False
    ).exclude(product__direct_supply=True).select_related('product__supplier', 'product__category').order_by(
        'product__category__name', 'product__name', 'label'
    )
    if category_filter:
        variants = variants.filter(product__category_id=category_filter)
    if supplier_filter:
        variants = variants.filter(product__supplier_id=supplier_filter)

    inv_map = {i.variant_id: i for i in StockistInventory.objects.filter(stockist=stockist)}

    if request.method == 'POST' and request.POST.get('action') == 'auto_restock_settings':
        # Stage 7g: opt-in auto-restock (threshold + reorder quantity).
        stockist.auto_restock_enabled = request.POST.get('enabled') == '1'
        try:
            stockist.auto_restock_threshold = max(0, int(request.POST.get('threshold', 5)))
            stockist.auto_restock_quantity = max(1, int(request.POST.get('quantity', 10)))
        except ValueError:
            pass
        try:
            from decimal import Decimal as _D, InvalidOperation as _IO
            raw_cap = (request.POST.get('max_price') or '0').strip()
            stockist.auto_restock_max_price = max(_D('0'), _D(raw_cap or '0'))
        except (_IO, ValueError):
            pass
        stockist.save(update_fields=['auto_restock_enabled', 'auto_restock_threshold',
                                     'auto_restock_quantity', 'auto_restock_max_price'])
        if stockist.auto_restock_enabled:
            from .auto_restock import check_stockist
            made = check_stockist(stockist)
            messages.success(
                request,
                'Auto-restock ON — at ≤'
                f'{stockist.auto_restock_threshold} units a supplier request for '
                f'{stockist.auto_restock_quantity} units is filed automatically.'
                + (f' {made} request(s) filed right now.' if made else '')
            )
        else:
            messages.success(request, 'Auto-restock turned off.')
        return redirect('stockist:inventory')

    if request.method == 'POST' and request.POST.get('toggle_auto'):
        # Per-item auto-restock switch — high-value items get ordered by hand.
        # (Sent as a named button inside the inventory grid form, so it must be
        # handled BEFORE the qty-save branch below.)
        variant = ProductVariant.objects.filter(pk=request.POST.get('toggle_auto')).first()
        if variant:
            row, _ = StockistInventory.objects.get_or_create(
                stockist=stockist, variant=variant, defaults={'quantity': 0}
            )
            row.auto_restock_exempt = not row.auto_restock_exempt
            row.save(update_fields=['auto_restock_exempt'])
            state = 'excluded from' if row.auto_restock_exempt else 'included in'
            messages.success(request, f'{variant.product.name} {variant.label} is now {state} auto-restock.')
        return redirect('stockist:inventory')

    if request.method == 'POST':
        # Stage 7 stock rule: manual edits may only REDUCE stock (offline sale,
        # own use, wastage). Increases come solely from supplier-fulfilled
        # restock requests — stock can never be typed into existence.
        blocked = []
        reduced = 0
        for variant in variants:
            key = f'qty_{variant.pk}'
            if key in request.POST:
                try:
                    qty = int(request.POST[key])
                except ValueError:
                    continue
                if qty < 0:
                    continue
                inv_obj, _ = StockistInventory.objects.get_or_create(
                    stockist=stockist, variant=variant, defaults={'quantity': 0}
                )
                if qty > inv_obj.quantity:
                    blocked.append(f'{variant.product.name} {variant.label}')
                elif qty < inv_obj.quantity:
                    inv_obj.quantity = qty
                    inv_obj.save()
                    reduced += 1
        if reduced:
            messages.success(request, f'Inventory reduced for {reduced} item(s).')
            from .auto_restock import check_stockist
            made = check_stockist(stockist)
            if made:
                messages.info(request, f'Auto-restock: {made} supplier request(s) filed.')
        if blocked:
            messages.error(
                request,
                'Stock can only be reduced manually. To increase, send a restock '
                'request to the supplier — blocked: ' + ', '.join(blocked[:5])
                + ('…' if len(blocked) > 5 else '')
            )
        return redirect('stockist:inventory')

    from decimal import Decimal
    from stockist.models import get_stockist_price
    inv_data = []
    total_value = Decimal('0')
    for v in variants:
        inv = inv_map.get(v.pk)
        qty = inv.quantity if inv else 0
        stockist_price, charges_breakdown = get_stockist_price(v.product, v, stockist)
        value = Decimal(qty) * stockist_price
        total_value += value
        inv_data.append({
            'variant': v,
            'quantity': qty,
            'auto_exempt': inv.auto_restock_exempt if inv else False,
            'supplier_price': v.selling_price,
            'stockist_price': stockist_price,
            'charges_breakdown': charges_breakdown,
            'stock_value': value,
            'updated_at': inv.updated_at if inv else None,
            'low_stock': 0 < qty <= 10,
        })

    from suppliers.models import Supplier
    categories = Category.objects.all().order_by('name')
    suppliers = Supplier.objects.filter(status='approved').order_by('company_name')

    return render(request, 'stockist/inventory.html', {
        'stockist': stockist,
        'inv_data': inv_data,
        'categories': categories,
        'suppliers': suppliers,
        'category_filter': category_filter,
        'supplier_filter': supplier_filter,
        'total_value': total_value,
        'in_stock': sum(1 for i in inv_data if i['quantity'] > 0),
        'low_stock_count': sum(1 for i in inv_data if i['low_stock']),
        'out_of_stock': sum(1 for i in inv_data if i['quantity'] == 0),
    })


@stockist_required
def processing_charges(request):
    """Manage stages and set charges at global, supplier, or product level."""
    from suppliers.models import Supplier
    from collections import defaultdict
    stockist = request.user.stockist_profile
    suppliers = Supplier.objects.filter(status='approved').order_by('company_name')
    products = Product.objects.filter(status='approved', is_deleted=False).select_related('supplier', 'category')

    # Auto-seed default stages if none exist
    if not StockistStage.objects.filter(stockist=stockist).exists():
        defaults = [
            ('Shipping / Transport', 'both', 0),
            ('Packing / Packaging', 'both', 1),
            ('Cleaning / Grading', 'product', 2),
            ('Shortage / Wastage', 'product', 3),
            ('Handling / Labour', 'both', 4),
            ('Other', 'both', 5),
        ]
        for name, display_in, order in defaults:
            StockistStage.objects.create(stockist=stockist, name=name, display_in=display_in, order=order)

    if request.method == 'POST':
        action = request.POST.get('action', '')

        # ── Stage management ──────────────────────────────────────────────────
        if action == 'add_stage':
            name = request.POST.get('stage_name', '').strip()
            display_in = request.POST.get('stage_display', 'both')
            if name:
                try:
                    StockistStage.objects.create(stockist=stockist, name=name, display_in=display_in)
                    messages.success(request, f'Stage "{name}" created.')
                except Exception:
                    messages.error(request, f'Stage "{name}" already exists.')
            else:
                messages.error(request, 'Stage name is required.')

        elif action == 'edit_stage':
            stage_id = request.POST.get('stage_id')
            name = request.POST.get('stage_name', '').strip()
            display_in = request.POST.get('stage_display', 'both')
            is_active = request.POST.get('stage_active') == '1'
            if name and stage_id:
                StockistStage.objects.filter(pk=stage_id, stockist=stockist).update(
                    name=name, display_in=display_in, is_active=is_active
                )
                messages.success(request, f'Stage "{name}" updated.')

        elif action == 'delete_stage':
            stage_id = request.POST.get('stage_id')
            stage = StockistStage.objects.filter(pk=stage_id, stockist=stockist).first()
            if stage:
                stage_name = stage.name
                stage.delete()
                messages.success(request, f'Stage "{stage_name}" deleted.')

        # ── Charge management ─────────────────────────────────────────────────
        elif action == 'save_charges':
            # Save all charges for a product or supplier from the grid
            scope = request.POST.get('scope', 'product')
            product_id = request.POST.get('product_id')
            supplier_id = request.POST.get('supplier_id')
            saved = 0

            product_obj = None
            supplier_obj = None
            if product_id:
                product_obj = Product.objects.filter(pk=product_id).first()
                if product_obj:
                    supplier_obj = product_obj.supplier
            elif supplier_id:
                supplier_obj = Supplier.objects.filter(pk=supplier_id).first()

            stages = StockistStage.objects.filter(stockist=stockist, is_active=True)
            for stage in stages:
                val_raw = request.POST.get(f'val_{stage.pk}', '').strip()
                charge_type = request.POST.get(f'type_{stage.pk}', 'fixed')
                note = request.POST.get(f'note_{stage.pk}', '').strip()
                charge_id = request.POST.get(f'cid_{stage.pk}', '').strip()

                if val_raw:
                    try:
                        charge_val = float(val_raw)
                        defaults = {
                            'charge_type': charge_type,
                            'charge_value': charge_val,
                            'description': note,
                            'is_active': True,
                            'scope': scope,
                            'supplier': supplier_obj,
                            'product': product_obj if scope == 'product' else None,
                        }
                        if charge_id:
                            ProcessingCharge.objects.filter(pk=charge_id, stockist=stockist).update(**defaults)
                        else:
                            ProcessingCharge.objects.create(stockist=stockist, stage_obj=stage, **defaults)
                        saved += 1
                    except Exception:
                        pass
                elif charge_id:
                    # Value cleared — delete the charge
                    ProcessingCharge.objects.filter(pk=charge_id, stockist=stockist).delete()

            messages.success(request, f'{saved} charge(s) saved.')

        elif action == 'delete_charge':
            charge_id = request.POST.get('charge_id')
            ProcessingCharge.objects.filter(pk=charge_id, stockist=stockist).delete()
            messages.success(request, 'Charge removed.')

        elif action == 'toggle_charge':
            charge_id = request.POST.get('charge_id')
            c = ProcessingCharge.objects.filter(pk=charge_id, stockist=stockist).first()
            if c:
                c.is_active = not c.is_active
                c.save()

        elif action == 'confirm':
            product_id = request.POST.get('product_id')
            product = Product.objects.filter(
                pk=product_id,
                status='approved',
                is_deleted=False,
            ).select_related('supplier').first()
            if not product:
                messages.error(request, 'Product not found or not eligible for release.')
            else:
                has_applicable_charges = ProcessingCharge.objects.filter(
                    stockist=stockist,
                    is_active=True,
                    stage_obj__is_active=True,
                    charge_value__gt=0,
                ).filter(
                    Q(scope='global')
                    | Q(scope='supplier', supplier_id=product.supplier_id)
                    | Q(scope='product', product_id=product.pk)
                ).exists()

                if not has_applicable_charges:
                    messages.error(
                        request,
                        f'Cannot release "{product.name}" without charges. Set at least one active charge first.',
                    )
                else:
                    product.charges_confirmed = True
                    product.save(update_fields=['charges_confirmed'])
                    messages.success(request, f'"{product.name}" released to distributors.')

        return redirect('stockist:processing_charges')

    # ── Context ───────────────────────────────────────────────────────────────
    stages = StockistStage.objects.filter(stockist=stockist)
    all_charges = ProcessingCharge.objects.filter(
        stockist=stockist
    ).select_related('product', 'supplier', 'stage_obj').order_by('scope')

    # Build charge lookup: (scope_key, stage_id) → charge
    charge_map = {}
    for c in all_charges:
        if not c.stage_obj:
            continue
        if c.scope == 'product' and c.product_id:
            charge_map[(f'p_{c.product_id}', c.stage_obj_id)] = c
        elif c.scope == 'supplier' and c.supplier_id:
            charge_map[(f's_{c.supplier_id}', c.stage_obj_id)] = c
        elif c.scope == 'global':
            charge_map[('global', c.stage_obj_id)] = c

    pending_release = Product.objects.filter(
        status='approved', charges_confirmed=False, is_deleted=False
    ).select_related('supplier', 'category').order_by('created_at')

    return render(request, 'stockist/processing_charges.html', {
        'products': products,
        'suppliers': suppliers,
        'stages': stages,
        'all_charges': all_charges,
        'charge_map_json': _charge_map_to_json(charge_map),
        'charge_type_choices': ProcessingCharge.CHARGE_TYPE_CHOICES,
        'pending_release': pending_release,
        'stage_display_choices': StockistStage.DISPLAY_CHOICES,
    })


def _charge_map_to_json(charge_map):
    """Serialize charge_map to JSON for JS consumption."""
    import json
    data = {}
    for (scope_key, stage_id), c in charge_map.items():
        key = f'{scope_key}_{stage_id}'
        data[key] = {
            'id': c.pk,
            'type': c.charge_type,
            'value': str(c.charge_value),
            'note': c.description,
            'active': c.is_active,
        }
    return json.dumps(data)


@stockist_required
def charges_overview(request):
    """Full-screen view: all products with their current charges at a glance."""
    from suppliers.models import Supplier
    stockist = request.user.stockist_profile

    # Handle bulk clear
    if request.method == 'POST' and request.POST.get('action') == 'clear_all':
        scope = request.POST.get('scope', 'product')
        if scope == 'product':
            count = ProcessingCharge.objects.filter(stockist=stockist, scope='product').delete()[0]
            messages.success(request, f'Cleared {count} product-level charge(s). Products now use supplier/global charges.')
        elif scope == 'supplier':
            count = ProcessingCharge.objects.filter(stockist=stockist, scope='supplier').delete()[0]
            messages.success(request, f'Cleared {count} supplier-level charge(s). Products now use global charges.')
        elif scope == 'all':
            count = ProcessingCharge.objects.filter(stockist=stockist, scope__in=['product', 'supplier']).delete()[0]
            messages.success(request, f'Cleared {count} charge(s). Only global charges remain.')
        return redirect('stockist:charges_overview')

    stages = StockistStage.objects.filter(stockist=stockist, is_active=True)
    products = Product.objects.filter(
        status='approved', is_deleted=False
    ).select_related('supplier', 'category').order_by('supplier__company_name', 'name')

    # Build charge lookup: product_id → stage_id → charge
    charges = ProcessingCharge.objects.filter(
        stockist=stockist, is_active=True
    ).select_related('stage_obj', 'product', 'supplier')

    # Per-product charges (product-level wins, then supplier, then global)
    product_charge_map = {}  # product_id → {stage_id: charge}
    supplier_charge_map = {}  # supplier_id → {stage_id: charge}
    global_charge_map = {}   # stage_id → charge

    for c in charges:
        if not c.stage_obj:
            continue
        if c.scope == 'product' and c.product_id:
            product_charge_map.setdefault(c.product_id, {})[c.stage_obj_id] = c
        elif c.scope == 'supplier' and c.supplier_id:
            supplier_charge_map.setdefault(c.supplier_id, {})[c.stage_obj_id] = c
        elif c.scope == 'global':
            global_charge_map[c.stage_obj_id] = c

    # Build rows: each product + resolved charge per stage
    rows = []
    for p in products:
        stage_charges = []
        for stage in stages:
            c = (product_charge_map.get(p.pk, {}).get(stage.pk)
                 or supplier_charge_map.get(p.supplier_id, {}).get(stage.pk)
                 or global_charge_map.get(stage.pk))
            stage_charges.append(c)
        rows.append({'product': p, 'stage_charges': stage_charges})

    supplier_filter = request.GET.get('supplier', '')
    if supplier_filter:
        rows = [r for r in rows if str(r['product'].supplier_id) == supplier_filter]

    suppliers = Supplier.objects.filter(status='approved').order_by('company_name')

    return render(request, 'stockist/charges_overview.html', {
        'rows': rows,
        'stages': stages,
        'suppliers': suppliers,
        'supplier_filter': supplier_filter,
    })


@stockist_required
def charges_download(request):
    """Download all products + charges as Excel for offline editing."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    from django.http import HttpResponse
    stockist = request.user.stockist_profile
    stages = list(StockistStage.objects.filter(stockist=stockist, is_active=True))
    products = Product.objects.filter(
        status='approved', is_deleted=False
    ).select_related('supplier', 'category').order_by('supplier__company_name', 'name')

    charges = ProcessingCharge.objects.filter(
        stockist=stockist, is_active=True
    ).select_related('stage_obj', 'product', 'supplier')

    product_charge_map = {}
    supplier_charge_map = {}
    global_charge_map = {}
    for c in charges:
        if not c.stage_obj:
            continue
        if c.scope == 'product' and c.product_id:
            product_charge_map.setdefault(c.product_id, {})[c.stage_obj_id] = c
        elif c.scope == 'supplier' and c.supplier_id:
            supplier_charge_map.setdefault(c.supplier_id, {})[c.stage_obj_id] = c
        elif c.scope == 'global':
            global_charge_map[c.stage_obj_id] = c

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Processing Charges'

    # Header
    header = ['product_id', 'Product Name', 'Supplier', 'Category', 'Confirmed']
    for s in stages:
        header += [f'{s.name} — Type', f'{s.name} — Value', f'{s.name} — Note']
    ws.append(header)

    # Style header
    hdr_fill = PatternFill('solid', fgColor='1B5E20')
    hdr_font = Font(bold=True, color='FFFFFF')
    for cell in ws[1]:
        cell.fill = hdr_fill
        cell.font = hdr_font
        cell.alignment = Alignment(horizontal='center')

    id_fill = PatternFill('solid', fgColor='EEEEEE')

    for p in products:
        row = [p.pk, p.name, p.supplier.company_name, p.category.name if p.category else '', 'Yes' if p.charges_confirmed else 'No']
        for s in stages:
            c = (product_charge_map.get(p.pk, {}).get(s.pk)
                 or supplier_charge_map.get(p.supplier_id, {}).get(s.pk)
                 or global_charge_map.get(s.pk))
            if c:
                row += [c.charge_type, float(c.charge_value), c.description]
            else:
                row += ['fixed', '', '']
        ws.append(row)
        ws.cell(ws.max_row, 1).fill = id_fill

    # Column widths
    ws.column_dimensions['A'].width = 10
    ws.column_dimensions['B'].width = 30
    ws.column_dimensions['C'].width = 20
    ws.column_dimensions['D'].width = 16
    ws.column_dimensions['E'].width = 12
    for i, s in enumerate(stages):
        col_start = 6 + i * 3
        for j in range(3):
            ws.column_dimensions[openpyxl.utils.get_column_letter(col_start + j)].width = 16

    # Instructions sheet
    inst = wb.create_sheet('Instructions')
    inst.append(['Column', 'Notes'])
    inst.append(['product_id', 'DO NOT edit — used to match rows on upload'])
    inst.append(['Confirmed', 'Read-only — set via Processing Charges page'])
    inst.append(['Stage — Type', 'fixed | percent | per_kg'])
    inst.append(['Stage — Value', 'Numeric value. Leave blank to remove charge.'])
    inst.append(['Stage — Note', 'Optional description'])
    inst.column_dimensions['A'].width = 20
    inst.column_dimensions['B'].width = 60

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="processing_charges.xlsx"'
    wb.save(response)
    return response


@stockist_required
def charges_upload(request):
    """Upload edited Excel and apply charges."""
    import openpyxl
    from decimal import Decimal, InvalidOperation
    stockist = request.user.stockist_profile

    if request.method != 'POST':
        return redirect('stockist:processing_charges')

    f = request.FILES.get('charges_file')
    if not f:
        messages.error(request, 'No file uploaded.')
        return redirect('stockist:charges_overview')

    try:
        wb = openpyxl.load_workbook(f, data_only=True)
        ws = wb.active
    except Exception as e:
        messages.error(request, f'Could not read file: {e}')
        return redirect('stockist:charges_overview')

    # Read header to find stage columns
    header = [str(cell.value or '').strip() for cell in ws[1]]
    stages = list(StockistStage.objects.filter(stockist=stockist, is_active=True))

    # Map stage name → (type_col_idx, value_col_idx, note_col_idx)
    stage_col_map = {}
    for s in stages:
        type_col = f'{s.name} — Type'
        val_col = f'{s.name} — Value'
        note_col = f'{s.name} — Note'
        if type_col in header and val_col in header:
            stage_col_map[s.pk] = (
                header.index(type_col),
                header.index(val_col),
                header.index(note_col) if note_col in header else None,
            )

    updated = skipped = errors = 0
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not row[0]:
            continue
        try:
            product_id = int(row[0])
            product_obj = Product.objects.get(pk=product_id, status='approved')
        except (ValueError, TypeError, Product.DoesNotExist):
            skipped += 1
            continue

        for s in stages:
            if s.pk not in stage_col_map:
                continue
            type_idx, val_idx, note_idx = stage_col_map[s.pk]
            charge_type = str(row[type_idx] or 'fixed').strip().lower()
            val_raw = row[val_idx]
            note = str(row[note_idx] or '').strip() if note_idx is not None else ''

            if charge_type not in ('fixed', 'percent', 'per_kg'):
                charge_type = 'fixed'

            if val_raw not in (None, ''):
                try:
                    charge_val = Decimal(str(val_raw))
                    ProcessingCharge.objects.update_or_create(
                        stockist=stockist, scope='product',
                        product=product_obj, stage_obj=s,
                        defaults={
                            'charge_type': charge_type,
                            'charge_value': charge_val,
                            'description': note,
                            'is_active': True,
                            'supplier': product_obj.supplier,
                        }
                    )
                    updated += 1
                except (InvalidOperation, Exception):
                    errors += 1
            else:
                # Blank value — remove product-level charge (falls back to supplier/global)
                ProcessingCharge.objects.filter(
                    stockist=stockist, scope='product',
                    product=product_obj, stage_obj=s
                ).delete()

    messages.success(request, f'Upload complete — {updated} charges updated, {skipped} skipped, {errors} errors.')
    return redirect('stockist:charges_overview')


@stockist_required
def restock_requests(request):
    """Stockist views and creates restock requests to supplier (via admin)."""
    stockist = request.user.stockist_profile
    requests_qs = StockistRestockRequest.objects.filter(
        stockist=stockist
    ).select_related('variant__product__supplier')

    if request.method == 'POST':
        variant_id = request.POST.get('variant_id')
        qty = request.POST.get('quantity', '0')
        note = request.POST.get('note', '').strip()
        try:
            variant = ProductVariant.objects.get(pk=variant_id)
            if variant.product.direct_supply:
                messages.error(request, 'Direct-supply products never pass through the stockist.')
                return redirect('stockist:restock_requests')
            StockistRestockRequest.objects.create(
                stockist=stockist, variant=variant,
                quantity_requested=int(qty), note=note
            )
            messages.success(request, f'Restock request submitted for {variant}.')
        except Exception as e:
            messages.error(request, str(e))
        return redirect('stockist:restock_requests')

    # All variants for restock request, highlight low stock ones
    all_variants = StockistInventory.objects.filter(
        stockist=stockist
    ).select_related('variant__product')

    low_variants = [inv for inv in all_variants if inv.quantity <= 10]

    return render(request, 'stockist/restock_requests.html', {
        'requests': requests_qs,
        'low_variants': low_variants,
        'all_variants': all_variants,
    })


# ── Order Fulfillment ─────────────────────────────────────────────────────────

@stockist_required
def order_list(request):
    """Stockist sees confirmed orders that need supplier pickup (fulfillment_source=supplier).
    Excludes orders where the distributor is also the supplier of the ordered products
    AND the customer belongs to that distributor (self-fulfilled orders).
    """
    from orders.models import Order
    stockist = request.user.stockist_profile
    status_filter = request.GET.get('status', '')

    # Stage 7 routing: only orders from marketplaces on THIS stockist's chain
    # (unassigned chains visible to every stockist as a fallback).
    from django.db.models import Q as _Q
    orders = Order.objects.filter(
        _Q(distributor__wholesaler__stockist=stockist) |
        _Q(distributor__isnull=True) |
        _Q(distributor__wholesaler__isnull=True) |
        _Q(distributor__wholesaler__stockist__isnull=True),
        fulfillment_source='supplier',
        status__in=['confirmed', 'stockist_picked'],
    ).select_related('customer', 'distributor').prefetch_related(
        'items__variant__product__supplier'
    ).order_by('-created_at')

    if status_filter:
        orders = orders.filter(status=status_filter)

    # Filter out orders where distributor is also the supplier for ALL items
    # (those are self-fulfilled — stockist has no involvement)
    filtered_orders = []
    for order in orders:
        # Skip-tier routing: the stockist only sees an order if AT LEAST ONE
        # of its supplier lines is routed to the stockist. If every line skips
        # the stockist (ships direct to distributor/shop/customer), the stockist
        # must neither see it nor be told about it.
        line_targets = [
            (item.product.delivery_target if item.product else 'stockist')
            for item in order.items.all()
        ]
        if line_targets and not any(t == 'stockist' for t in line_targets):
            continue

        if not order.distributor:
            filtered_orders.append(order)
            continue
        dist_user = order.distributor.user
        # Check if distributor is also a supplier
        if 'supplier' not in dist_user.get_roles_list():
            filtered_orders.append(order)
            continue
        # Check if all items in this order are from the distributor's own supplier profile
        try:
            dist_supplier = dist_user.supplier_profile
        except Exception:
            filtered_orders.append(order)
            continue
        # If any item is NOT from the distributor-supplier, stockist still handles it
        all_self_supplied = all(
            item.variant and item.variant.product.supplier_id == dist_supplier.pk
            for item in order.items.all()
        )
        if not all_self_supplied:
            filtered_orders.append(order)
        # else: all items are from distributor's own supplier — skip (no stockist involvement)

    return render(request, 'stockist/order_list.html', {
        'stockist': stockist,
        'orders': filtered_orders,
        'status_filter': status_filter,
        'pending_pickup': sum(1 for o in filtered_orders if o.status == 'confirmed'),
        'in_transit': sum(1 for o in filtered_orders if o.status == 'stockist_picked'),
    })


@stockist_required
def order_detail(request, pk):
    """Stockist views order detail and can pick up or deliver to distributor."""
    from orders.models import Order
    from django.db.models import Prefetch
    from orders.models import OrderItem
    stockist = request.user.stockist_profile
    order = get_object_or_404(
        Order.objects.prefetch_related(Prefetch(
            'items',
            queryset=OrderItem.objects.select_related(
                'product', 'product__supplier', 'variant', 'supplier_shipment'),
        )),
        pk=pk, fulfillment_source='supplier',
    )

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'pick' and order.status == 'confirmed':
            # Stockist has picked items from supplier
            order.status = 'stockist_picked'
            order.save()
            sync_order_ledger(order)
            try:
                from lakrish_mart.activity_logger import act as _act
                _act.order('stockist_picked', order=order, by=request.user)
            except Exception:
                pass
            messages.success(request, f'Order #{order.id} marked as picked from supplier. Head to distributor.')

        elif action == 'deliver' and order.status == 'stockist_picked':
            order.status = 'at_distributor'
            order.save()
            try:
                from lakrish_mart.activity_logger import act as _act
                _act.order('at_distributor', order=order, by=request.user)
            except Exception:
                pass
            if order.distributor:
                from distributors.models import DistributorStock, DistributorStockBatch
                for item in order.items.all():
                    if item.variant:
                        stock_obj, _ = DistributorStock.objects.get_or_create(
                            distributor=order.distributor, variant=item.variant,
                            defaults={'quantity': 0}
                        )
                        # Create a new batch at the cost price paid for this order
                        DistributorStockBatch.objects.create(
                            stock=stock_obj,
                            quantity=item.quantity,
                            cost_price=item.base_price,
                            note=f'Order #{order.pk}',
                        )
                        stock_obj.refresh_quantity()
            sync_order_ledger(order)
            messages.success(request, f'Order #{order.id} delivered to distributor. Stock updated.')

        return redirect('stockist:order_detail', pk=pk)

    # Lines routed to THIS stockist that the supplier has not packed yet.
    # Soft hint only - the stockist is never blocked from picking up.
    not_ready_count = 0
    for it in order.items.all():
        s = getattr(it, 'supplier_shipment', None)
        if s and s.target == 'stockist' and s.status not in ('confirmed', 'dispatched', 'received'):
            not_ready_count += 1

    return render(request, 'stockist/order_detail.html', {
        'stockist': stockist,
        'order': order,
        'not_ready_count': not_ready_count,
    })


@stockist_required
def distributor_restock(request):
    """Stockist sees restock requests from distributors and can fulfill them."""
    from django.db.models import Q
    from distributors.models import RestockRequest, DistributorStock, DistributorStockBatch
    stockist = request.user.stockist_profile
    status_filter = request.GET.get('status', 'pending')

    # Stage 7 routing: a Distributor buys from exactly ONE stockist, so this
    # stockist only sees requests from marketplaces on ITS chain. Unassigned
    # chains stay visible to every stockist (single-stockist installs).
    # Direct-supply products never pass through a stockist at all.
    mine = (
        Q(distributor__wholesaler__stockist=stockist) |
        Q(distributor__wholesaler__isnull=True) |
        Q(distributor__wholesaler__stockist__isnull=True)
    )
    requests_qs = RestockRequest.objects.filter(
        mine,
        status__in=['pending', 'approved'] if not status_filter else [status_filter],
    ).exclude(variant__product__direct_supply=True).select_related(
        'variant__product__supplier', 'distributor'
    ).order_by('-requested_at')

    # ── Stage 7: wholesale Distributor restock requests (this stockist only) ──
    # Delivering one is the ONLY way WholesalerStock ever increases.
    from distributors.wholesaler_models import WholesalerRestockRequest, WholesalerStock
    if request.method == 'POST' and request.POST.get('kind') == 'wholesaler':
        from django.utils import timezone
        req_id = request.POST.get('req_id')
        action = request.POST.get('action')
        wreq = WholesalerRestockRequest.objects.filter(
            stockist=stockist, status='pending'
        ).select_related('wholesaler', 'variant__product').filter(pk=req_id).first()
        if wreq is None:
            messages.error(request, 'Request not found.')
        elif action == 'deliver':
            ws, _ = WholesalerStock.objects.get_or_create(
                wholesaler=wreq.wholesaler, variant=wreq.variant, defaults={'quantity': 0}
            )
            ws.quantity += wreq.quantity_requested
            ws.save()
            wreq.status = 'delivered'
            wreq.resolved_at = timezone.now()
            wreq.save()
            messages.success(
                request,
                f'{wreq.quantity_requested}× {wreq.variant.label} of '
                f'{wreq.variant.product.name} delivered to {wreq.wholesaler.company_name}.'
            )
        elif action == 'reject':
            from django.utils import timezone as _tz
            wreq.status = 'rejected'
            wreq.resolved_at = _tz.now()
            wreq.save()
            messages.warning(request, f'Distributor request #{req_id} rejected.')
        return redirect('stockist:distributor_restock')

    if request.method == 'POST':
        req_id = request.POST.get('req_id')
        action = request.POST.get('action')
        try:
            restock_req = RestockRequest.objects.filter(mine).get(pk=req_id)
            if action == 'approve':
                restock_req.status = 'approved'
                fm = request.POST.get('fulfillment_method', '').strip()
                if fm:
                    restock_req.fulfillment_method = fm
                restock_req.save()
                messages.success(request, f'Request #{req_id} approved. Collect from supplier and deliver.')
            elif action == 'picked':
                restock_req.status = 'stockist_picked'
                restock_req.save()
                messages.success(request, f'Request #{req_id} marked as picked from supplier. Deliver to distributor.')
            elif action == 'deliver':
                variant = restock_req.variant
                dist = restock_req.distributor
                if restock_req.loose_grams:
                    # Stage 7: LOOSE delivery — grams land in the loose pool.
                    # This request flow is the only way loose stock increases.
                    from distributors.models import DistributorLooseStock
                    ls, _ = DistributorLooseStock.objects.get_or_create(
                        distributor=dist, product=variant.product,
                        defaults={'quantity_grams': 0, 'cost_price_per_gram': 0},
                    )
                    ls.quantity_grams += restock_req.loose_grams
                    if variant.unit_grams:
                        ls.cost_price_per_gram = variant.selling_price / variant.unit_grams
                    ls.save()
                    delivered_what = f'{restock_req.loose_grams}g loose {variant.product.name}'
                else:
                    # Packed delivery — add to DistributorStock batch
                    stock_obj, _ = DistributorStock.objects.get_or_create(
                        distributor=dist, variant=variant, defaults={'quantity': 0}
                    )
                    DistributorStockBatch.objects.create(
                        stock=stock_obj,
                        quantity=restock_req.quantity_requested,
                        cost_price=variant.selling_price,
                        note=f'Restock request #{restock_req.pk} fulfilled by stockist',
                    )
                    stock_obj.refresh_quantity()
                    delivered_what = (f'{restock_req.quantity_requested}× {variant.label} '
                                      f'of {variant.product.name}')
                restock_req.status = 'delivered'
                restock_req.save()
                messages.success(request, f'{delivered_what} delivered to {dist.company_name}.')
            elif action == 'reject':
                restock_req.status = 'rejected'
                restock_req.save()
                messages.warning(request, f'Request #{req_id} rejected.')
        except RestockRequest.DoesNotExist:
            messages.error(request, 'Request not found.')
        return redirect('stockist:distributor_restock')

    pending_count = RestockRequest.objects.filter(mine, status='pending').exclude(
        variant__product__direct_supply=True
    ).count()
    wholesaler_requests = WholesalerRestockRequest.objects.filter(
        stockist=stockist
    ).select_related('wholesaler', 'variant__product').order_by('-requested_at')[:50]

    return render(request, 'stockist/distributor_restock.html', {
        'requests': requests_qs,
        'wholesaler_requests': wholesaler_requests,
        'status_filter': status_filter,
        'status_choices': RestockRequest.STATUS_CHOICES,
        'pending_count': pending_count,
    })


@stockist_required
def direct_supply(request):
    """Stage 7: the stockist decides what bypasses them (full control).

    Products marked DIRECT go supplier → marketplace: the stockist neither
    processes, stocks, prices nor earns on them. Products turned back to
    NORMAL re-enter the chain and wait for this stockist's charges again.
    Global flag — one product is either direct for everyone or for no one.
    """
    products = Product.objects.filter(
        status__in=['approved', 'pending'], is_deleted=False
    ).select_related('supplier', 'category').order_by('category__name', 'name')

    if request.method == 'POST':
        product = products.filter(pk=request.POST.get('product_id')).first()
        if product is None:
            messages.error(request, 'Product not found.')
        else:
            product.direct_supply = not product.direct_supply
            product.save(update_fields=['direct_supply'])
            if product.direct_supply:
                messages.success(
                    request,
                    f'"{product.name}" is now DIRECT: the supplier delivers it '
                    'straight to shops — no processing, no charges, no commission.'
                )
            else:
                messages.success(
                    request,
                    f'"{product.name}" is back in the chain — it needs your '
                    'processing charges before shops can see it.'
                )
        return redirect('stockist:direct_supply')

    direct_count = sum(1 for p in products if p.direct_supply)
    return render(request, 'stockist/direct_supply.html', {
        'products': products,
        'direct_count': direct_count,
        'chain_count': len(list(products)) - direct_count,
    })
