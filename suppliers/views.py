from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db.models import Sum
from functools import wraps
from accounts.decorators import ensure_user_role
from products.models import Product, ProductVariant, ProductBundle, ProductVariantType
from products.forms import ProductForm, VariantFormSet
from orders.models import OrderItem
from .models import Supplier


def _save_bundles(product, request):
    """Reconcile a product's wholesale bundles from the posted form.

    Bundles arrive as parallel arrays so the Add/Edit page can add any number
    without a Django formset:
        bundle_id[]          existing ProductBundle pk ('' for new rows)
        bundle_name[]        label shown to wholesale buyers
        bundle_unit_grams[]  which pack variant the bundle is made of
        bundle_qty[]         units of that pack inside one bundle

    Price and availability are computed from the underlying pack — the supplier
    does not enter them. Rows the supplier removed are deactivated (never
    hard-deleted) so any historic order lines keep resolving. When the product's
    'Allow Bundle' flag is off, nothing is posted and every bundle is deactivated.
    """
    if not product.allow_bundle:
        ProductBundle.objects.filter(product=product, is_active=True).update(is_active=False)
        return

    ids    = request.POST.getlist('bundle_id[]')
    names  = request.POST.getlist('bundle_name[]')
    ugs    = request.POST.getlist('bundle_unit_grams[]')
    qtys   = request.POST.getlist('bundle_qty[]')

    # Map this product's packs by unit_grams so a bundle can bind to one.
    variants_by_ug = {}
    for v in product.variants.filter(is_active=True):
        variants_by_ug.setdefault(v.unit_grams, v)

    kept_pks = set()
    for i in range(len(names)):
        name = (names[i] if i < len(names) else '').strip()
        if not name:
            continue
        try:
            ug = int(ugs[i]) if i < len(ugs) and ugs[i] else 0
            qty = int(qtys[i]) if i < len(qtys) and qtys[i] else 0
        except (ValueError, TypeError):
            continue
        variant = variants_by_ug.get(ug)
        if variant is None or qty <= 0:
            continue

        pk = ids[i] if i < len(ids) and ids[i] else None
        fields = dict(
            variant=variant, name=name[:80], pack_quantity=qty, is_active=True,
        )
        if pk:
            ProductBundle.objects.filter(pk=pk, product=product).update(**fields)
            kept_pks.add(int(pk))
        else:
            nb = ProductBundle.objects.create(product=product, **fields)
            kept_pks.add(nb.pk)

    # Deactivate bundles the supplier dropped (keep the row + any history).
    ProductBundle.objects.filter(product=product).exclude(pk__in=kept_pks).update(is_active=False)


def _validate_variant_prices(product, groups, mrp_on=None, suggest_on=None):
    """Enforce the price-ordering rules on a parsed variants payload.

      • When MRP is on:       selling price < MRP  (strictly; equal rejected).
      • When Suggested is on:  selling < suggested, and (if MRP on) suggested ≤ MRP.

    mrp_on / suggest_on override the stored flags (pass the POSTed toggle values
    when validating a save, since the flags on `product` aren't updated yet).
    Raises ValueError with a combined message if any pack violates the rules.
    """
    from decimal import Decimal, InvalidOperation

    def _d(v):
        try:
            return Decimal(str(v)) if v not in (None, '', 'null') else None
        except (InvalidOperation, ValueError, TypeError):
            return None

    mrp_on = bool(product.show_mrp) if mrp_on is None else bool(mrp_on)
    suggest_on = bool(product.show_suggested_price) if suggest_on is None else bool(suggest_on)
    errors = []
    for g in (groups or [])[:5]:
        if not isinstance(g, dict):
            continue
        vname = (g.get('name') or 'Variant')
        for pk_data in (g.get('packs') or []):
            if not isinstance(pk_data, dict):
                continue
            price = _d(pk_data.get('selling_price'))
            if price is None or price <= 0:
                continue
            mrp = _d(pk_data.get('mrp'))
            sug = _d(pk_data.get('suggested_price'))
            auto = _d(pk_data.get('auto_price'))
            tag = f"{vname} · {pk_data.get('label', '')}"
            # The auto price is the minimum floor — a manual pack price may be
            # equal or higher, never lower. (Client blocks this too; this is the
            # server-side safety net against a stale/crafted POST.)
            if auto is not None and auto > 0 and price < auto:
                errors.append(f"{tag}: price \u20b9{price} is below the auto price \u20b9{auto} (set \u20b9{auto} or higher).")
            if mrp_on and mrp is not None and not (price < mrp):
                errors.append(f"{tag}: selling \u20b9{price} must be LESS than MRP \u20b9{mrp} (not equal).")
            if suggest_on and sug is not None:
                if not (sug > price):
                    errors.append(f"{tag}: suggested \u20b9{sug} must be MORE than your price \u20b9{price}.")
                if mrp_on and mrp is not None and sug > mrp:
                    errors.append(f"{tag}: suggested \u20b9{sug} cannot exceed MRP \u20b9{mrp}.")
    if errors:
        raise ValueError('Price rules violated — ' + '; '.join(errors))


def _save_product_variants(product, request):
    """Variant-aware pack/bundle save from a single JSON payload.

    The Add/Edit page posts `variants_json`: a list of variant groups, each with
    its own packs (and per-pack bundle count). Shape:

        [
          {"id": 12 or null, "name": "Premium",
           "packs": [
             {"id": 5 or null, "label": "1kg", "unit_grams": 1000,
              "selling_price": "150.00", "mrp": "195.00",
              "suggested_price": null, "stock": 40, "bundle_qty": 12},
             ...
           ]},
          ...
        ]

    Reconciled without hard-deletes: existing rows are updated in place (keeping
    their pk so orders/prices/history survive); dropped rows are deactivated.
    A single-variant product keeps its variant name blank (no storefront picker).
    Returns True if it handled the save, False if no payload was present.
    """
    import json
    from decimal import Decimal, InvalidOperation

    raw = request.POST.get('variants_json')
    if not raw:
        return False
    try:
        groups = json.loads(raw)
    except (ValueError, TypeError):
        return False
    if not isinstance(groups, list) or not groups:
        return False

    groups = groups[:5]                         # hard cap: max 5 variants
    single = len(groups) == 1

    def _dec(val):
        try:
            return Decimal(str(val)) if val not in (None, '', 'null') else None
        except (InvalidOperation, ValueError, TypeError):
            return None

    def _int(val):
        try:
            return int(val)
        except (ValueError, TypeError):
            return 0

    # Validate price ordering BEFORE writing anything (raises ValueError).
    _validate_variant_prices(product, groups)

    existing_types = {vt.pk: vt for vt in product.variant_types.all()}
    kept_type_pks = set()
    kept_pack_pks = set()
    kept_bundle_pks = set()

    for pos, g in enumerate(groups):
        if not isinstance(g, dict):
            continue
        name = '' if single else (g.get('name') or '').strip()[:60]

        # Authoritative loose pool for this variant (base units). Only update it
        # when the client sent a value (the supplier edited this variant's pool);
        # None means "leave the stored pool as-is".
        pool_grams = g.get('pool_grams')
        try:
            pool_grams = int(pool_grams) if pool_grams is not None else None
        except (ValueError, TypeError):
            pool_grams = None

        vt = existing_types.get(g.get('id'))
        if vt is None:
            vt = ProductVariantType.objects.create(
                product=product, name=name, position=pos, is_active=True,
                master_stock_grams=max(0, pool_grams) if pool_grams is not None else 0,
            )
        else:
            vt.name = name
            vt.position = pos
            vt.is_active = True
            _vt_fields = ['name', 'position', 'is_active']
            if pool_grams is not None:
                vt.master_stock_grams = max(0, pool_grams)
                _vt_fields.append('master_stock_grams')
            vt.save(update_fields=_vt_fields)
        kept_type_pks.add(vt.pk)

        # Existing packs of this variant, bucketed by unit_grams for matching.
        existing_packs = {}
        for p in ProductVariant.objects.filter(variant_type=vt):
            existing_packs.setdefault(p.unit_grams, []).append(p)

        for pk_data in (g.get('packs') or []):
            if not isinstance(pk_data, dict):
                continue
            price = _dec(pk_data.get('selling_price'))
            if price is None or price <= 0:
                continue
            ug = _int(pk_data.get('unit_grams'))
            # When a toggle is OFF, its values are CLEARED (not just hidden) so
            # re-enabling later starts blank and the supplier re-enters them.
            mrp_val = _dec(pk_data.get('mrp')) if product.show_mrp else None
            sug_val = _dec(pk_data.get('suggested_price')) if product.show_suggested_price else None
            fields = dict(
                label=(pk_data.get('label') or '')[:50],
                selling_price=price,
                mrp=mrp_val,
                suggested_price=sug_val,
                stock=max(0, _int(pk_data.get('stock'))),
                unit_grams=ug,
                is_active=True,
            )
            bucket = existing_packs.get(ug) or []
            target = bucket.pop(0) if bucket else None
            if target:
                for k, val in fields.items():
                    setattr(target, k, val)
                target.save()
                pack = target
            else:
                pack = ProductVariant.objects.create(
                    product=product, variant_type=vt, **fields
                )
            kept_pack_pks.add(pack.pk)

            # Per-pack bundle (only when the product allows bundles).
            bqty = _int(pk_data.get('bundle_qty'))
            if product.allow_bundle and bqty > 0:
                b = ProductBundle.objects.filter(
                    product=product, variant=pack, is_active=True
                ).first()
                if b:
                    b.name = f'Box of {bqty}'
                    b.pack_quantity = bqty
                    b.save(update_fields=['name', 'pack_quantity'])
                else:
                    b = ProductBundle.objects.create(
                        product=product, variant=pack,
                        name=f'Box of {bqty}', pack_quantity=bqty, is_active=True,
                    )
                kept_bundle_pks.add(b.pk)

    # Deactivate dropped packs, bundles and variant types (never hard-delete).
    ProductVariant.objects.filter(product=product).exclude(
        pk__in=kept_pack_pks).update(is_active=False)
    ProductBundle.objects.filter(product=product).exclude(
        pk__in=kept_bundle_pks).update(is_active=False)
    ProductVariantType.objects.filter(product=product).exclude(
        pk__in=kept_type_pks).update(is_active=False)
    return True


def supplier_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        denial = ensure_user_role(
            request,
            ('supplier',),
            set_active_role='supplier',
        )
        if denial:
            return denial
        try:
            supplier = request.user.supplier_profile
            if supplier.status != 'approved':
                return render(request, 'suppliers/pending.html', {'supplier': supplier})
        except Supplier.DoesNotExist:
            messages.error(request, 'Your supplier profile is missing. Please contact admin to create it.')
            return redirect('accounts:pick_role')
        return view_func(request, *args, **kwargs)
    return wrapper


def _save_image_from_url(product, url):
    """Download image from URL and save to product with security limits."""
    try:
        import re
        import urllib.request
        from django.core.files.base import ContentFile

        # Reject non-http URLs to prevent SSRF against internal services
        if not re.match(r'^https?://', url, re.IGNORECASE):
            return

        MAX_BYTES = 10 * 1024 * 1024  # 10 MB hard cap
        ALLOWED_CONTENT_TYPES = {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}

        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            content_type = resp.headers.get_content_type()
            if content_type not in ALLOWED_CONTENT_TYPES:
                return  # Reject non-image content types
            data = b''
            for chunk in iter(lambda: resp.read(65536), b''):
                data += chunk
                if len(data) > MAX_BYTES:
                    return  # Abort if file is too large

        if not data:
            return

        # Verify it is a valid image using Pillow (prevents malicious payloads)
        try:
            from PIL import Image
            from io import BytesIO
            img = Image.open(BytesIO(data))
            img.verify()
        except Exception:
            return  # Not a valid image

        # Use a safe filename based on product id, never user-supplied name
        ext_map = {'image/jpeg': '.jpg', 'image/png': '.png',
                   'image/webp': '.webp', 'image/gif': '.gif'}
        ext = ext_map.get(content_type, '.jpg')
        fname = f"product_{product.pk}{ext}"
        product.image.save(fname, ContentFile(data), save=True)
    except Exception:
        pass  # silently skip if image fetch fails


@supplier_required
def update_address(request):
    if request.method == 'POST':
        supplier = request.user.supplier_profile
        supplier.address = request.POST.get('address', '').strip()
        supplier.save(update_fields=['address'])
        messages.success(request, 'Pickup address updated.')
    return redirect('suppliers:dashboard')


@supplier_required
def dashboard(request):
    from decimal import Decimal
    from django.db.models import F, Q, Count, DecimalField, ExpressionWrapper
    from products.models import ProductVariant

    supplier = request.user.supplier_profile
    products = Product.objects.filter(supplier=supplier, is_deleted=False).prefetch_related('variants')
    recent_orders = OrderItem.objects.filter(
        product__supplier=supplier
    ).select_related('order', 'product', 'variant').order_by('-order__created_at')[:10]

    # ── Financial: order-value earnings (matches the Payout History page) ──
    # supplier_price × qty, with the variant selling_price as a fallback when
    # supplier_price was not frozen. Delivered = earned; the rest = pending.
    from django.db.models.functions import Coalesce
    from django.db.models import Case, When
    unit_val = Case(
        When(supplier_price__gt=0, then=F('supplier_price')),
        default=Coalesce(F('variant__selling_price'), Decimal('0')),
        output_field=DecimalField(max_digits=16, decimal_places=2),
    )
    line_val = ExpressionWrapper(unit_val * F('quantity'),
                                 output_field=DecimalField(max_digits=16, decimal_places=2))
    oi = OrderItem.objects.filter(product__supplier=supplier).exclude(order__status='cancelled')
    earn = oi.aggregate(
        earned=Sum(line_val, filter=Q(order__status='delivered')),
        pending=Sum(line_val, filter=~Q(order__status='delivered')),
    )
    earned_delivered = earn['earned'] or 0
    pending_undelivered = earn['pending'] or 0

    # ── Financial: real settlements owed to the supplier (Payments Center) ──
    from payments.models import RoleSettlement
    settle = RoleSettlement.objects.filter(
        supplier=supplier, stage='stockist_to_supplier'
    ).aggregate(
        settled=Sum('amount_paid', filter=Q(status='confirmed')),
        awaiting=Sum('amount_due', filter=Q(status__in=('pending', 'submitted', 'disputed'))),
    )
    settled_amount = settle['settled'] or 0
    awaiting_settlement = settle['awaiting'] or 0

    # ── Financial: B2B procurement receivable from stockists ──
    from stockist.procurement_models import StockistSupplierAccount
    procurement_receivable = StockistSupplierAccount.objects.filter(
        supplier=supplier
    ).aggregate(t=Sum('balance_due'))['t'] or 0

    # ── Overall stock value = Σ (pack stock × selling price) ──
    _stock_qs = ProductVariant.objects.filter(
        product__supplier=supplier, product__is_deleted=False, is_active=True)
    stock_value = _stock_qs.aggregate(
        total=Sum(ExpressionWrapper(F('stock') * F('selling_price'),
                  output_field=DecimalField(max_digits=16, decimal_places=2)))
    )['total'] or 0
    total_units = _stock_qs.aggregate(total=Sum('stock'))['total'] or 0

    # ── Action-required alerts ──
    from distributors.models import RestockRequest
    try:
        from stockist.models import StockistRestockRequest
        stockist_restock_pending = StockistRestockRequest.objects.filter(
            variant__product__supplier=supplier, status='pending').count()
    except Exception:
        stockist_restock_pending = 0
    restock_pending = RestockRequest.objects.filter(
        variant__product__supplier=supplier, status='pending').count()
    disputed_settlements = RoleSettlement.objects.filter(
        supplier=supplier, stage='stockist_to_supplier', status='disputed').count()
    alert_total = restock_pending + stockist_restock_pending + disputed_settlements

    # ── Shipment status counters (real SupplierShipment statuses) ──
    from orders.models import SupplierShipment
    ship_rows = (SupplierShipment.objects.filter(supplier=supplier)
                 .exclude(order_item__order__status='cancelled')
                 .values('status').annotate(n=Count('id')))
    ship_counts = {r['status']: r['n'] for r in ship_rows}
    # Ordered list of (key, label) for display — mirrors SupplierShipment.STATUS_CHOICES.
    shipment_stages = [
        ('awaiting', 'Awaiting'), ('delayed', 'Delayed'), ('confirmed', 'Ready'),
        ('dispatched', 'Dispatched'), ('received', 'Received'), ('unavailable', 'Unavailable'),
    ]
    shipment_counters = [
        {'key': k, 'label': lbl, 'count': ship_counts.get(k, 0)} for k, lbl in shipment_stages
    ]

    return render(request, 'suppliers/dashboard.html', {
        'supplier': supplier,
        'products': products,
        'recent_orders': recent_orders,
        'product_count': products.count(),
        # financial
        'earned_delivered': earned_delivered,
        'pending_undelivered': pending_undelivered,
        'settled_amount': settled_amount,
        'awaiting_settlement': awaiting_settlement,
        'procurement_receivable': procurement_receivable,
        # inventory
        'stock_value': stock_value,
        'total_units': total_units,
        # alerts
        'restock_pending': restock_pending,
        'stockist_restock_pending': stockist_restock_pending,
        'disputed_settlements': disputed_settlements,
        'alert_total': alert_total,
        # shipments
        'shipment_counters': shipment_counters,
    })


@supplier_required
def product_list(request):
    supplier = request.user.supplier_profile
    category_filter = request.GET.get('category', '')
    status_filter = request.GET.get('status', '')
    products = Product.objects.filter(supplier=supplier, is_deleted=False).prefetch_related('variants').order_by('-created_at')
    if category_filter:
        products = products.filter(category_id=category_filter)
    if status_filter:
        products = products.filter(status=status_filter)

    # Deduplicate variants per product (keep latest by label)
    from products.models import Category
    categories = Category.objects.filter(products__supplier=supplier).distinct().order_by('name')

    # Build the product-grouped display/stock data (shared with the admin
    # Stock page — see products.stock_display.annotate_products_for_stock).
    from products.stock_display import annotate_products_for_stock, attach_downstream_detail
    products_with_variants = annotate_products_for_stock(products)
    attach_downstream_detail(products_with_variants)

    return render(request, 'suppliers/product_list.html', {
        'products': products_with_variants,
        'categories': categories,
        'category_filter': category_filter,
        'status_filter': status_filter,
        'status_choices': Product.STATUS_CHOICES,
    })


@supplier_required
def product_add(request):
    supplier = request.user.supplier_profile
    form = ProductForm(request.POST or None, request.FILES or None)
    formset = VariantFormSet(request.POST or None, prefix='variants')
    # The variant-aware path posts `variants_json` and bypasses the formset;
    # otherwise require the legacy formset to have >=1 valid pack.
    use_json = bool(request.POST.get('variants_json'))
    # Require BOTH the product form AND at least one valid pack size before we
    # persist anything — otherwise an invalid/empty variant set would leave a
    # "zombie" product with no packs (and no price). validate_min=1 on the
    # formset enforces the at-least-one-pack rule server-side.
    if request.method == 'POST' and form.is_valid() and (use_json or formset.is_valid()):
        product = form.save(commit=False)
        product.supplier = supplier

        # Auto-approve only if this supplier has their OWN distributor profile
        # Single-role policy: a supplier account is ONLY a supplier — it can
        # never own a store, so every supplier product goes through the normal
        # admin-approval path. (A shop listing its OWN goods does that from its
        # Marketplace login; see distributors/own_product_views.py.)
        product.self_distribute_only = False
        product.status = 'pending'

        product.show_mrp = request.POST.get('show_mrp_field') == 'true'
        # MRP (hard cap) and a suggested price (guidance) may both be on: the
        # shop cannot exceed the MRP, and the suggested price cautions it to
        # keep the price reasonable.
        product.show_suggested_price = request.POST.get('show_suggested_field') == 'true'
        product.allow_loose_distribution = request.POST.get('allow_loose_distribution') == '1'
        product.allow_bundle = request.POST.get('allow_bundle') == '1'
        # Supplier shipment routing (admin may override later at approval).
        _valid_targets = {c[0] for c in Product.DELIVERY_TARGET_CHOICES}
        _valid_modes = {c[0] for c in Product.SHIPMENT_MODE_CHOICES}
        _tgt = request.POST.get('delivery_target', 'stockist')
        _mode = request.POST.get('shipment_mode', 'collect')
        product.delivery_target = _tgt if _tgt in _valid_targets else 'stockist'
        product.shipment_mode = _mode if _mode in _valid_modes else 'collect'
        product.save()
        # Handle web image URL if no file uploaded
        if not request.FILES.get('image'):
            image_url = request.POST.get('image_url', '').strip()
            if image_url:
                _save_image_from_url(product, image_url)
        # Variant-aware save (new path). Falls back to the legacy formset save
        # if no variant JSON was posted. Price-rule violations raise ValueError.
        try:
            if not _save_product_variants(product, request):
                formset.instance = product
                formset.save()
                # Attach the freshly-saved packs to the product's default variant
                # type (created by the signal/backfill) so the layer stays consistent.
                vt = product.variant_types.first()
                if vt is None:
                    vt = ProductVariantType.objects.create(product=product, name='', position=0)
                ProductVariant.objects.filter(
                    product=product, variant_type__isnull=True
                ).update(variant_type=vt)
                if product.master_stock_grams and product.pricing_mode == 'auto':
                    product.sync_variant_stocks()
                _save_bundles(product, request)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect('suppliers:product_edit', pk=product.pk)

        messages.success(request, 'Product submitted for admin approval.')
        try:
            from lakrish_mart.activity_logger import act as _act
            _act.product('submitted_for_approval', product=product, by=request.user)
        except Exception:
            pass
        return redirect('suppliers:product_list')
    # A POST that failed validation (e.g. no valid pack sizes) falls through
    # here and re-renders with the bound form/formset so errors are visible.
    if request.method == 'POST':
        messages.error(request, 'Please fix the errors below — the product was not saved.')
    return render(request, 'suppliers/product_form.html', {
        'form': form, 'formset': formset, 'title': 'Add Product',
        'existing_variants_json': '[]',
        'existing_bundles_json': '[]',
        'variant_groups_json': '[]',
        'delivery_target_choices': Product.DELIVERY_TARGET_CHOICES,
        'shipment_mode_choices': Product.SHIPMENT_MODE_CHOICES,
    })


@supplier_required
def product_edit(request, pk):
    supplier = request.user.supplier_profile
    product = get_object_or_404(Product, pk=pk, supplier=supplier)
    form = ProductForm(request.POST or None, request.FILES or None, instance=product)
    formset = VariantFormSet(request.POST or None, instance=product, prefix='variants')
    if request.method == 'POST' and form.is_valid():
        # Variant-aware path posts `variants_json` and does NOT use the formset,
        # so skip formset validation entirely in that case.
        use_json = bool(request.POST.get('variants_json'))
        # Price-rule check up-front (server-side safety net) — flags the product's
        # current MRP/suggested toggles against the submitted packs. On failure we
        # re-render with the error and touch nothing.
        if use_json:
            import json as _json
            try:
                _parsed = _json.loads(request.POST.get('variants_json') or '[]')
            except (ValueError, TypeError):
                _parsed = []
            try:
                _validate_variant_prices(
                    product, _parsed,
                    mrp_on=request.POST.get('show_mrp_field') == 'true',
                    suggest_on=request.POST.get('show_suggested_field') == 'true',
                )
            except ValueError as exc:
                messages.error(request, str(exc))
                return redirect('suppliers:product_edit', pk=product.pk)
        # The JS rebuilds the whole pack table on every save, so treat every
        # submitted pack as new (INITIAL_FORMS=0, ids blanked) and VALIDATE it
        # BEFORE touching the DB. Nothing is saved unless the new pack set is
        # valid — an invalid edit must never destroy the existing packs.
        post_data = request.POST.copy()
        post_data['variants-INITIAL_FORMS'] = '0'
        total = int(post_data.get('variants-TOTAL_FORMS', 0) or 0)
        for i in range(total):
            post_data[f'variants-{i}-id'] = ''
        clean_formset = VariantFormSet(post_data, instance=product, prefix='variants')

        if not use_json and not clean_formset.is_valid():
            messages.error(request, 'Please fix the errors below — no changes were saved.')
        else:
            from django.db import transaction
            from products.models import ProductVariant
            with transaction.atomic():
                product = form.save()
                product.show_mrp = request.POST.get('show_mrp_field') == 'true'
                # Both may be on: MRP caps, suggested price guides (see product_add).
                product.show_suggested_price = request.POST.get('show_suggested_field') == 'true'
                product.allow_loose_distribution = request.POST.get('allow_loose_distribution') == '1'
                product.allow_bundle = request.POST.get('allow_bundle') == '1'
                # Supplier shipment routing (admin may override later at approval).
                _valid_targets = {c[0] for c in Product.DELIVERY_TARGET_CHOICES}
                _valid_modes = {c[0] for c in Product.SHIPMENT_MODE_CHOICES}
                _tgt = request.POST.get('delivery_target', 'stockist')
                _mode = request.POST.get('shipment_mode', 'collect')
                product.delivery_target = _tgt if _tgt in _valid_targets else 'stockist'
                product.shipment_mode = _mode if _mode in _valid_modes else 'collect'
                product.save(update_fields=['show_mrp', 'show_suggested_price',
                                            'allow_loose_distribution', 'allow_bundle',
                                            'delivery_target', 'shipment_mode'])
                # Handle web image URL if no file uploaded
                if not request.FILES.get('image'):
                    image_url = request.POST.get('image_url', '').strip()
                    if image_url:
                        _save_image_from_url(product, image_url)

                # Variant-aware save (new path). Falls back to the legacy
                # single-variant formset reconciliation if no JSON was posted.
                if not _save_product_variants(product, request):
                    # ── Legacy path: reconcile packs by size WITHOUT hard-deleting.
                    existing = {}
                    for v in ProductVariant.objects.filter(product=product):
                        existing.setdefault(v.unit_grams, []).append(v)
                    kept_pks = set()
                    for f in clean_formset.forms:
                        cd = getattr(f, 'cleaned_data', None)
                        if not cd or cd.get('DELETE') or cd.get('selling_price') is None:
                            continue
                        ug = cd.get('unit_grams') or 0
                        bucket = existing.get(ug) or []
                        target = bucket.pop(0) if bucket else None
                        fields = dict(
                            label=cd.get('label', ''), selling_price=cd['selling_price'],
                            mrp=cd.get('mrp'), suggested_price=cd.get('suggested_price'),
                            stock=cd.get('stock') or 0, is_active=True,
                        )
                        if target:
                            for key, val in fields.items():
                                setattr(target, key, val)
                            target.save()
                            kept_pks.add(target.pk)
                        else:
                            nv = ProductVariant.objects.create(product=product, unit_grams=ug, **fields)
                            kept_pks.add(nv.pk)
                    ProductVariant.objects.filter(product=product).exclude(pk__in=kept_pks).update(is_active=False)
                    _save_bundles(product, request)

                # Re-sync per-variant stock/price from the master pool ONLY when
                # the supplier did NOT submit explicit per-pack prices. When
                # `variants_json` is present, each pack's price was typed by the
                # supplier and already saved above — running sync here would
                # overwrite those hand-entered prices with
                # unit_grams × master_price_per_unit (the bug that silently reset
                # a lowered pack price, e.g. to ₹1.00).
                if (not use_json
                        and product.master_stock_grams
                        and product.pricing_mode == 'auto'):
                    product.sync_variant_stocks()
            messages.success(request, 'Product updated.')
            return redirect('suppliers:product_list')

    import json
    # Legacy single-pack list (kept for the existing JS pre-load of pack buttons
    # — it drives the FIRST variant's table on load).
    existing_variants = json.dumps(list(
        product.variants.filter(is_active=True, unit_grams__gt=0)
        .values('label', 'unit_grams', 'selling_price', 'mrp', 'suggested_price', 'stock')
    ), default=str)
    existing_bundles = json.dumps([
        {
            'id': b.pk, 'name': b.name, 'unit_grams': b.variant.unit_grams,
            'pack_quantity': b.pack_quantity,
            'price': str(b.price), 'available': b.available_bundles(),
        }
        for b in product.bundles.filter(is_active=True).select_related('variant')
    ], default=str)

    # Variant-grouped structure the new switcher UI consumes.
    _bundle_by_variant = {}
    for b in product.bundles.filter(is_active=True).select_related('variant'):
        _bundle_by_variant[b.variant_id] = b.pack_quantity
    variant_groups = []
    for vt in product.variant_types.filter(is_active=True).order_by('position', 'id'):
        packs = []
        for p in vt.packs.filter(is_active=True).order_by('selling_price'):
            packs.append({
                'id': p.pk, 'label': p.label, 'unit_grams': p.unit_grams,
                'selling_price': str(p.selling_price),
                'mrp': str(p.mrp) if p.mrp is not None else None,
                'suggested_price': str(p.suggested_price) if p.suggested_price is not None else None,
                'stock': p.stock,
                'bundle_qty': _bundle_by_variant.get(p.pk, 0),
            })
        variant_groups.append({
            'id': vt.pk, 'name': vt.name, 'packs': packs,
            # Authoritative loose pool for THIS variant (base units, g/ml/qty).
            # Sent so the edit form shows the real pool instead of reconstructing
            # it by summing per-pack counts (which multiplied the pool by the
            # number of packs). 0 means "no pool set yet".
            'pool_grams': vt.master_stock_grams or 0,
        })
    variant_groups_json = json.dumps(variant_groups, default=str)

    return render(request, 'suppliers/product_form.html', {
        'form': form, 'formset': formset, 'title': 'Edit Product',
        'existing_variants_json': existing_variants,
        'existing_bundles_json': existing_bundles,
        'variant_groups_json': variant_groups_json,
        'product': product,
        'delivery_target_choices': Product.DELIVERY_TARGET_CHOICES,
        'shipment_mode_choices': Product.SHIPMENT_MODE_CHOICES,
    })


@supplier_required
def product_delete(request, pk):
    supplier = request.user.supplier_profile
    product = get_object_or_404(Product, pk=pk, supplier=supplier, is_deleted=False)
    if request.method == 'POST':
        product.soft_delete()
        messages.success(request, f'"{product.name}" removed from your catalogue.')
        return redirect('suppliers:product_list')
    return render(request, 'suppliers/product_confirm_delete.html', {'product': product})


# Base units per whole display unit, keyed by the Product.master_unit code.
# 'g'  → kg  (1000 g),  'ml' → litre (1000 ml),  'qty' → pieces (1:1).
_POOL_UNIT = {
    'g':   ('kg', 1000),
    'ml':  ('litre', 1000),
    'qty': ('pieces', 1),
}


def _is_pool_product(product):
    """A product whose pack stocks are auto-derived from ONE shared pool.

    LOOSE products are pool-based: each variant holds a single stock pool and
    its pack counts auto-populate from it (pool // unit_grams). This is driven
    by the Loose flag — independent of pricing mode. (The old auto-pricing pool
    is still honoured as a fallback for non-loose auto products.)
    """
    return bool(product.allow_loose_distribution) or (
        product.master_stock_grams > 0 and product.pricing_mode == 'auto'
    )


@supplier_required
def stock_manage(request):
    """Supplier can update stock for all their variants from one page.

    Products come in two shapes:
      • Pool products (master_stock_grams>0, pricing_mode='auto') — every pack
        stock is derived from ONE shared pool. We edit the pool (in kg / litre
        / pieces); the per-pack counts are shown read-only.
      • Manual products — each pack carries its own independent stock count,
        edited per row as before.
    """
    from decimal import Decimal, InvalidOperation
    from django.urls import reverse
    from products.models import Category, Product, ProductBundle

    supplier = request.user.supplier_profile
    category_filter = request.GET.get('category', '')

    variants = ProductVariant.objects.filter(
        product__supplier=supplier, is_active=True
    ).select_related('product__category', 'variant_type').order_by(
        'product__category__name', 'product__name',
        'variant_type__position', 'selling_price'
    )
    if category_filter:
        variants = variants.filter(product__category_id=category_filter)

    # Group the on-screen packs by product, then by variant-type within each.
    # For LOOSE products each variant is pool-based: one editable pool, packs
    # read-only (auto-derived). For non-loose, packs are edited per row.
    products_ctx = []           # ordered list of {product, is_pool, ...}
    by_product = {}             # product_pk -> ctx dict (for POST lookup too)
    for v in variants:
        ctx = by_product.get(v.product_id)
        if ctx is None:
            is_pool = _is_pool_product(v.product)
            disp_unit, factor = _POOL_UNIT.get(v.product.master_unit, ('units', 1))
            ctx = {
                'product': v.product,
                'is_pool': is_pool,          # loose → per-variant pool editing
                'pool_unit': disp_unit,
                'pool_factor': factor,
                'variants': [],              # flat list of packs (POST handling)
                'variant_groups': [],        # [{vt, name, is_pool, pool_display, packs}]
                '_vg_index': {},             # vt_id -> group dict
                'bundles': [],
            }
            by_product[v.product_id] = ctx
            products_ctx.append(ctx)
        ctx['variants'].append(v)
        # Bucket this pack under its variant-type for display.
        vt = v.variant_type
        vt_id = v.variant_type_id
        grp = ctx['_vg_index'].get(vt_id)
        if grp is None:
            # The variant's own pool (grams) → shown in display units (e.g. kg).
            vt_pool = (vt.master_stock_grams if vt else 0) or 0
            grp = {
                'vt_id': vt_id,
                'name': (vt.name if vt else '') or '',
                'is_pool': ctx['is_pool'],
                'pool_display': (Decimal(vt_pool) / ctx['pool_factor'])
                                if ctx['is_pool'] else None,
                'packs': [],
            }
            ctx['_vg_index'][vt_id] = grp
            ctx['variant_groups'].append(grp)
        grp['packs'].append(v)

    # Show per-variant headers only when a product has more than one variant.
    for ctx in products_ctx:
        ctx['has_variants'] = len(ctx['variant_groups']) > 1

    # ── Stock-health flags for the quick-filter bar (All / Out / Low) ──────
    # Thresholds: a pack is "out" at 0 units, "low" at <= LOW_PACK_UNITS units.
    # A loose pool is "out" at 0 and "low" at <= LOW_POOL_BASE base units.
    LOW_PACK_UNITS = 10                 # packs / pieces: <=10 units is low
    LOW_POOL_DISPLAY = 5                 # loose pool: <=5 kg/litre is low (display units)
    out_count = low_count = 0
    for ctx in products_ctx:
        has_out = has_low = False
        if ctx['is_pool']:
            # Judge loose products by each variant's pool. pool_display is already
            # in display units (kg / litre / pieces) — no extra query needed.
            is_qty = ctx['product'].master_unit == 'qty'
            low_cutoff = LOW_PACK_UNITS if is_qty else LOW_POOL_DISPLAY
            for grp in ctx['variant_groups']:
                pool = float(grp.get('pool_display') or 0)
                if pool <= 0:
                    has_out = True
                elif pool <= low_cutoff:
                    has_low = True
        else:
            for v in ctx['variants']:
                if v.stock <= 0:
                    has_out = True
                elif v.stock <= LOW_PACK_UNITS:
                    has_low = True
        ctx['has_out'] = has_out
        ctx['has_low'] = has_low
        if has_out:
            out_count += 1
        if has_low:
            low_count += 1

    # Attach each product's wholesale bundles (one extra query, not N).
    if by_product:
        bundles = ProductBundle.objects.filter(
            product_id__in=by_product.keys(), is_active=True
        ).select_related('variant').order_by('product_id', 'pack_quantity')
        for b in bundles:
            ctx = by_product.get(b.product_id)
            if ctx is not None:
                b.available = b.available_bundles()   # computed for the template
                ctx['bundles'].append(b)

    if request.method == 'POST':
        # Upper bound that catches fat-finger typos (e.g. an extra digit) while
        # still allowing genuinely large warehouse quantities.
        MAX_STOCK = 1_000_000          # per-pack cap (manual products)
        MAX_POOL_BASE = 100_000_000    # pool cap in base units (~100k kg)

        updated = 0        # pack rows / pools that actually changed
        skipped = []       # (label, reason) for anything rejected

        for ctx in products_ctx:
            product = ctx['product']

            if ctx['is_pool']:
                # LOOSE product: one pool PER VARIANT. Reading pool_vt_<id>, we
                # set that variant's master_stock_grams and auto-populate its
                # pack counts (pool // unit_grams).
                from products.models import ProductVariantType
                for grp in ctx['variant_groups']:
                    key = f"pool_vt_{grp['vt_id']}"
                    if key not in request.POST:
                        continue
                    raw = (request.POST.get(key) or '').strip()
                    label = f"{product.name}" + (f" · {grp['name']}" if ctx['has_variants'] else '')
                    try:
                        amount = Decimal(raw)
                    except (InvalidOperation, ValueError, TypeError):
                        skipped.append((label, 'not a valid number'))
                        continue
                    if amount < 0:
                        skipped.append((label, 'cannot be negative'))
                        continue
                    new_base = int(amount * ctx['pool_factor'])
                    if new_base > MAX_POOL_BASE:
                        skipped.append((label, 'stock is unrealistically large'))
                        continue
                    vt = ProductVariantType.objects.filter(
                        pk=grp['vt_id'], product=product).first()
                    if vt is None or new_base == vt.master_stock_grams:
                        continue
                    vt.master_stock_grams = new_base
                    vt.save(update_fields=['master_stock_grams'])
                    vt.sync_pack_stocks()     # auto-populate this variant's packs
                    updated += 1
                continue

            # Non-loose product — each pack keeps its own stock count.
            for variant in ctx['variants']:
                key = f'stock_{variant.pk}'
                if key not in request.POST:
                    continue
                raw = (request.POST.get(key) or '').strip()
                label = f'{product.name} ({variant.label})'
                try:
                    new_stock = int(raw)
                except (ValueError, TypeError):
                    skipped.append((label, 'not a whole number'))
                    continue
                if new_stock < 0:
                    skipped.append((label, 'cannot be negative'))
                    continue
                if new_stock > MAX_STOCK:
                    skipped.append((label, f'exceeds the {MAX_STOCK:,} limit'))
                    continue
                if new_stock == variant.stock:
                    continue
                variant.stock = new_stock
                variant.save(update_fields=['stock'])
                updated += 1

            # Bundles carry no stock of their own — availability is computed
            # from the pack stock above, so nothing to save here.

        if updated:
            messages.success(request, f'Stock updated for {updated} product(s)/pack(s).')
        elif not skipped:
            messages.info(request, 'No stock changes to save.')

        if skipped:
            details = '; '.join(f'{name} — {reason}' for name, reason in skipped)
            messages.warning(
                request,
                f'{len(skipped)} item(s) were NOT saved: {details}. '
                'Please re-check those values.'
            )

        # Keep the supplier on the same filtered view after saving.
        url = reverse('suppliers:stock_manage')
        if category_filter:
            url += f'?category={category_filter}'
        return redirect(url)

    # Only categories this supplier actually sells in.
    categories = Category.objects.filter(
        products__supplier=supplier, products__is_deleted=False
    ).distinct().order_by('name')

    # Downstream stock per product (stockist + distributor + shop), so the
    # supplier can see where their goods sit after leaving them. One aggregated
    # query per tier — see products.stock_display.attach_downstream_stock.
    from products.stock_display import attach_downstream_stock
    _ds_products = [ctx['product'] for ctx in products_ctx]
    attach_downstream_stock(_ds_products)
    for ctx in products_ctx:
        pp = ctx['product']
        ctx['ds_stockist'] = pp.ds_stockist
        ctx['ds_distributor'] = pp.ds_distributor
        ctx['ds_shop'] = pp.ds_shop
        ctx['ds_total'] = pp.ds_total

    return render(request, 'suppliers/stock_manage.html', {
        'products_ctx': products_ctx,
        'variant_count': len(variants),
        'categories': categories,
        'category_filter': category_filter,
        'out_count': out_count,
        'low_count': low_count,
    })


SHIP_TARGETS = ('stockist', 'distributor', 'shop', 'customer')
SHIP_PER_PAGE = 15          # order cards per page
SHIP_OVERDUE_DAYS = 3       # awaiting this long (or more) is flagged red


def _ensure_shipment(order_item, supplier):
    """Get (or lazily create) the SupplierShipment for one supplier order line.

    Normally the row already exists (created by the OrderItem post_save signal);
    this is a safety net for single-line POST actions.
    """
    from orders.models import SupplierShipment
    prod = order_item.product
    ship, _created = SupplierShipment.objects.get_or_create(
        order_item=order_item,
        defaults={
            'supplier': supplier,
            'target': getattr(prod, 'delivery_target', 'stockist') or 'stockist',
            'mode': supplier.effective_shipment_mode(prod),
            'status': 'awaiting',
        },
    )
    return ship


def _backfill_shipments(base_items, supplier):
    """Create shipment rows for legacy order lines that predate the signal.

    One cheap EXISTS query in the normal case (nothing missing); a single
    bulk_create when there is something to backfill.
    """
    from orders.models import SupplierShipment
    missing = list(base_items.filter(supplier_shipment__isnull=True))
    if not missing:
        return
    SupplierShipment.objects.bulk_create([
        SupplierShipment(
            order_item=it, supplier=supplier,
            target=getattr(it.product, 'delivery_target', 'stockist') or 'stockist',
            mode=supplier.effective_shipment_mode(it.product),
            status='awaiting',
        ) for it in missing
    ])


def _reassert_modes(supplier):
    """Keep still-awaiting lines in sync with the supplier's effective mode.

    Global switch wins; otherwise each product's own mode. Set-based UPDATEs
    (no per-line loop) - they touch zero rows when nothing is out of sync.
    """
    from django.utils import timezone
    from orders.models import SupplierShipment
    now = timezone.now()
    awaiting = SupplierShipment.objects.filter(
        supplier=supplier, status__in=['awaiting', 'delayed'])
    if supplier.use_global_shipment_mode:
        gm = supplier.default_shipment_mode
        awaiting.exclude(mode=gm).update(mode=gm, updated_at=now)
    else:
        for m in ('collect', 'send'):
            awaiting.filter(order_item__product__shipment_mode=m).exclude(mode=m)\
                .update(mode=m, updated_at=now)


def _log_ship(request, event, order, detail=''):
    try:
        from lakrish_mart.activity_logger import act as _act
        _act.order(event, order=order, by=request.user, detail=detail)
    except Exception:
        pass


def _dest_suggestion(order, target, cache):
    """Who/where a line is going - prefilled into the dispatch form.

    The customer's name/address/phone is only ever returned when the target is
    'customer' (that is the one case the supplier legitimately delivers to them).
    """
    key = (order.pk, target)
    if key in cache:
        return cache[key]

    def join(*parts):
        return ', '.join(str(p).strip() for p in parts if p and str(p).strip())

    out = ''
    try:
        dist = order.distributor
        if target == 'customer':
            c = order.customer
            out = join(c.get_full_name() or c.username, order.address, order.phone)
        elif target == 'shop' and dist:
            out = join(dist.company_name, dist.address, dist.phone)
        elif target == 'distributor':
            w = dist.wholesaler if dist else None
            if w:
                out = join(w.company_name, w.area, w.phone)
        elif target == 'stockist':
            w = dist.wholesaler if dist else None
            st = w.stockist if w else None
            if st is None:
                if '_fallback_stockist' not in cache:
                    from stockist.models import Stockist
                    cache['_fallback_stockist'] = (
                        Stockist.objects.filter(status='active').order_by('pk').first()
                    )
                st = cache['_fallback_stockist']
            if st:
                out = join(st.company_name, st.address, st.phone)
    except Exception:
        out = ''
    cache[key] = out
    return out


def _parse_date(value):
    import datetime
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _orders_post(request, supplier, base_items):
    """All POST actions of the supplier orders page. Always redirects back to
    the same URL (tab, filters and page are preserved)."""
    from django.db.models import DateTimeField, Value
    from django.db.models.functions import Coalesce
    from django.utils import timezone
    from orders.models import SupplierShipment

    back = request.get_full_path()
    action = request.POST.get('action')
    now = timezone.now()

    # ── Global shipment-mode switch ─────────────────────────────────────────
    if action == 'set_global_mode':
        supplier.use_global_shipment_mode = request.POST.get('use_global') == '1'
        gm = request.POST.get('default_shipment_mode', 'collect')
        if gm in {c[0] for c in Supplier.SHIPMENT_MODE_CHOICES}:
            supplier.default_shipment_mode = gm
        supplier.save(update_fields=['use_global_shipment_mode', 'default_shipment_mode'])
        _reassert_modes(supplier)
        if supplier.use_global_shipment_mode:
            messages.success(
                request,
                f'Global mode set to "{supplier.get_default_shipment_mode_display()}". '
                f'It now applies to all products.')
        else:
            messages.success(request, 'Global mode off - each product uses its own setting.')
        return redirect(back)

    # ── Bulk ready: mark many lines (collect OR send) as packed, any orders ──
    if action == 'bulk_ready':
        ids = {int(i) for i in request.POST.getlist('item_ids') if i.isdigit()}
        lines = list(base_items.filter(
            pk__in=ids, supplier_shipment__status__in=['awaiting', 'delayed']))
        if not lines:
            messages.info(request, 'Nothing to mark ready - the selected lines are no longer awaiting.')
            return redirect(back)
        SupplierShipment.objects.filter(
            order_item_id__in=[it.pk for it in lines], status__in=['awaiting', 'delayed']
        ).update(status='confirmed', confirmed_at=now, updated_at=now,
                 issue_reason='', issue_note='', expected_ready_on=None, issue_at=None)
        for oid in sorted({it.order_id for it in lines}):
            order = next(it.order for it in lines if it.order_id == oid)
            names = ', '.join(sorted({it.product.name for it in lines if it.order_id == oid}))
            _log_ship(request, 'supplier_ready', order, f'BULK {names}')
        skipped = len(ids) - len(lines)
        msg = f'Marked {len(lines)} line(s) ready across {len({it.order_id for it in lines})} order(s).'
        if skipped:
            msg += f' {skipped} skipped (already handled).'
        messages.success(request, msg)
        return redirect(back)

    # ── Bulk dispatch: one docket per destination within an order ───────────
    # Only lines that are READY (packed) can be dispatched.
    if action == 'bulk_dispatch':
        order_id = request.POST.get('order_id', '')
        target = request.POST.get('target', '')
        if not order_id.isdigit() or target not in SHIP_TARGETS:
            messages.error(request, 'Invalid bulk dispatch request.')
            return redirect(back)
        carrier = request.POST.get('carrier', '').strip()
        ref = request.POST.get('tracking_reference', '').strip()
        lines = list(base_items.filter(
            order_id=int(order_id),
            supplier_shipment__target=target,
            supplier_shipment__mode='send',
            supplier_shipment__status='confirmed',
        ))
        if not lines:
            messages.info(request, 'No ready send-mode lines for that destination. Mark them ready first.')
            return redirect(back)
        SupplierShipment.objects.filter(order_item_id__in=[it.pk for it in lines]).update(
            dispatched_to_name=request.POST.get('dispatched_to_name', '').strip(),
            carrier=carrier,
            tracking_reference=ref,
            transhipment_note=request.POST.get('transhipment_note', '').strip(),
            status='dispatched',
            dispatched_at=now,
            confirmed_at=Coalesce('confirmed_at', Value(now, output_field=DateTimeField())),
            updated_at=now,
        )
        _log_ship(request, 'supplier_dispatched', lines[0].order,
                  f'BULK {len(lines)} line(s) to {target} carrier={carrier} ref={ref}')
        messages.success(
            request,
            f'Dispatched {len(lines)} line(s) of Order #{order_id} to '
            f'{dict(SupplierShipment.TARGET_CHOICES).get(target, target)} '
            f'under docket "{ref or carrier or "-"}".')
        return redirect(back)

    # ── Single-line actions ─────────────────────────────────────────────────
    item_id = request.POST.get('item_id', '')
    oi = base_items.filter(pk=int(item_id)).first() if item_id.isdigit() else None
    if oi is None:
        messages.error(request, 'That order line was not found.')
        return redirect(back)
    ship = _ensure_shipment(oi, supplier)
    issue_fields = ['status', 'issue_reason', 'issue_note', 'expected_ready_on',
                    'issue_at', 'resume_requested_at', 'updated_at']

    if action == 'ready' and ship.status in ('awaiting', 'delayed'):
        ship.status = 'confirmed'
        ship.confirmed_at = now
        ship.clear_issue()
        ship.save(update_fields=issue_fields + ['confirmed_at'])
        _log_ship(request, 'supplier_ready', oi.order,
                  f'item#{oi.pk} {oi.product.name} -> {ship.get_target_display()}')
        messages.success(
            request,
            f'"{oi.product.name}" (Order #{oi.order_id}) is ready. '
            + ('The receiver can collect it.' if ship.mode == 'collect'
               else 'Now enter dispatch details.'))

    elif action == 'unready' and ship.status == 'confirmed':
        ship.status = 'awaiting'
        ship.confirmed_at = None
        ship.save(update_fields=['status', 'confirmed_at', 'updated_at'])
        _log_ship(request, 'supplier_unready', oi.order,
                  f'item#{oi.pk} {oi.product.name} reverted to awaiting')
        messages.success(request, f'"{oi.product.name}" (Order #{oi.order_id}) is back to awaiting.')

    elif action == 'undispatch' and ship.status == 'dispatched':
        # Back to READY (it is still packed); the docket details are cleared.
        ship.status = 'confirmed'
        ship.dispatched_at = None
        ship.dispatched_to_name = ''
        ship.carrier = ''
        ship.tracking_reference = ''
        ship.transhipment_note = ''
        ship.save()
        _log_ship(request, 'supplier_undispatched', oi.order,
                  f'item#{oi.pk} {oi.product.name} dispatch reverted to ready')
        messages.success(request, f'Dispatch of "{oi.product.name}" reverted. It is back to ready.')

    elif action == 'dispatch' and ship.mode == 'send' and ship.status == 'confirmed':
        ship.dispatched_to_name = request.POST.get('dispatched_to_name', '').strip()
        ship.carrier = request.POST.get('carrier', '').strip()
        ship.tracking_reference = request.POST.get('tracking_reference', '').strip()
        ship.transhipment_note = request.POST.get('transhipment_note', '').strip()
        ship.status = 'dispatched'
        ship.dispatched_at = now
        ship.save()
        _log_ship(request, 'supplier_dispatched', oi.order,
                  f'item#{oi.pk} {oi.product.name} -> {ship.get_target_display()} '
                  f'carrier={ship.carrier} ref={ship.tracking_reference}')
        messages.success(request, f'Dispatched "{oi.product.name}" for Order #{oi.order_id}.')

    elif action == 'dispatch' and ship.mode == 'send' and ship.status in ('awaiting', 'delayed'):
        messages.warning(request, f'Mark "{oi.product.name}" ready (packed) before dispatching it.')

    elif action == 'delay' and ship.status in ('awaiting', 'delayed'):
        reason = request.POST.get('reason', '')
        if reason not in {k for k, _ in SupplierShipment.DELAY_REASONS}:
            messages.error(request, 'Pick a reason for the delay.')
            return redirect(back)
        expected = _parse_date(request.POST.get('expected_ready_on', ''))
        if expected and expected < timezone.localdate():
            messages.error(request, 'The expected date cannot be in the past.')
            return redirect(back)
        ship.status = 'delayed'
        ship.issue_reason = reason
        ship.issue_note = request.POST.get('issue_note', '').strip()[:1000]
        ship.expected_ready_on = expected
        ship.issue_at = now
        ship.save(update_fields=issue_fields)
        _log_ship(request, 'supplier_delayed', oi.order,
                  f'item#{oi.pk} {oi.product.name} reason={reason} expected={expected or "-"}')
        messages.success(
            request,
            f'"{oi.product.name}" moved to the Delayed tab. The customer and receiver can see the reason'
            + (f' and the expected date ({expected:%d %b}).' if expected else '.'))

    elif action == 'unavailable' and ship.status in ('awaiting', 'delayed', 'confirmed'):
        reason = request.POST.get('reason', '')
        if reason not in {k for k, _ in SupplierShipment.UNAVAILABLE_REASONS}:
            messages.error(request, 'Pick a reason.')
            return redirect(back)
        ship.status = 'unavailable'
        ship.confirmed_at = None
        ship.issue_reason = reason
        ship.issue_note = request.POST.get('issue_note', '').strip()[:1000]
        ship.expected_ready_on = None
        ship.issue_at = now
        ship.save(update_fields=issue_fields + ['confirmed_at'])
        _log_ship(request, 'supplier_unavailable', oi.order,
                  f'item#{oi.pk} {oi.product.name} reason={reason}')
        messages.warning(
            request,
            f'"{oi.product.name}" (Order #{oi.order_id}) is marked unavailable. '
            f'The shop and admin can see it and will decide what to do.')

    elif action == 'available' and (
            ship.status == 'delayed'
            or (ship.status == 'unavailable' and ship.within_undo_window)):
        # Clearing a delay is harmless, and so is undoing a fresh (accidental)
        # "unavailable". Both go straight back to awaiting.
        ship.status = 'awaiting'
        ship.clear_issue()
        ship.save(update_fields=issue_fields)
        _log_ship(request, 'supplier_available_again', oi.order,
                  f'item#{oi.pk} {oi.product.name} back to awaiting')
        messages.success(request, f'"{oi.product.name}" is back to awaiting.')

    elif action == 'available' and ship.status == 'unavailable':
        # Flagged a while ago: the customer may no longer want it and only the
        # shop can tell. Ask the shop instead of resuming on our own.
        if ship.resume_requested_at:
            messages.info(request, 'You already asked the shop to confirm. Waiting for their answer.')
        else:
            ship.resume_requested_at = now
            ship.save(update_fields=['resume_requested_at', 'updated_at'])
            _log_ship(request, 'supplier_resume_requested', oi.order,
                      f'item#{oi.pk} {oi.product.name} can be supplied again; waiting for shop')
            messages.info(
                request,
                f'Request sent. "{oi.product.name}" returns to your list once the shop confirms '
                f'the customer still wants it.')

    elif action == 'withdraw_resume' and ship.status == 'unavailable' and ship.resume_requested_at:
        ship.resume_requested_at = None
        ship.save(update_fields=['resume_requested_at', 'updated_at'])
        _log_ship(request, 'supplier_resume_withdrawn', oi.order,
                  f'item#{oi.pk} {oi.product.name} resume request withdrawn')
        messages.success(request, 'Request withdrawn. The line stays unavailable.')

    else:
        messages.info(request, 'No change - that action was not applicable.')
    return redirect(back)


@supplier_required
def orders(request):
    """Supplier shipment requests: per-order, per-product, with packing summary,
    bulk actions, filters, search, sorting and pagination."""
    import csv
    from urllib.parse import urlencode
    from django.core.paginator import Paginator
    from django.http import HttpResponse
    from django.utils import timezone
    from orders.models import SupplierShipment

    supplier = request.user.supplier_profile

    # This supplier's lines (cancelled orders excluded). select_related pulls
    # everything the page needs so rendering does no per-line queries.
    base_items = (
        OrderItem.objects.filter(product__supplier=supplier)
        .exclude(order__status='cancelled')
        .select_related(
            'order', 'order__customer', 'order__distributor',
            'order__distributor__wholesaler', 'order__distributor__wholesaler__stockist',
            'product', 'variant',
        )
    )

    # Set-based housekeeping (zero rows touched in the normal case).
    _backfill_shipments(base_items, supplier)
    _reassert_modes(supplier)

    if request.method == 'POST':
        return _orders_post(request, supplier, base_items)

    # ── Filters / search / sort (all GET, all validated) ────────────────────
    f_product = request.GET.get('product', '').strip()
    f_from = request.GET.get('from', '').strip()
    f_to = request.GET.get('to', '').strip()
    f_target = request.GET.get('target', '').strip()
    f_q = request.GET.get('q', '').strip()
    f_sort = request.GET.get('sort', 'old').strip()
    if f_sort not in ('old', 'new'):
        f_sort = 'old'

    qs = base_items
    if f_product.isdigit():
        qs = qs.filter(product_id=int(f_product))
    else:
        f_product = ''
    d_from, d_to = _parse_date(f_from), _parse_date(f_to)
    if d_from:
        qs = qs.filter(order__created_at__date__gte=d_from)
    else:
        f_from = ''
    if d_to:
        qs = qs.filter(order__created_at__date__lte=d_to)
    else:
        f_to = ''
    if f_target in SHIP_TARGETS:
        qs = qs.filter(supplier_shipment__target=f_target)
    else:
        f_target = ''
    q_digits = f_q.lstrip('#').strip()
    if q_digits.isdigit():
        qs = qs.filter(order_id=int(q_digits))
    elif f_q:
        qs = qs.none()

    qs = qs.order_by('-order__created_at', '-order_id') if f_sort == 'new' \
        else qs.order_by('order__created_at', 'order_id')
    items = list(qs)

    ships_all = {s.order_item_id: s for s in SupplierShipment.objects.filter(
        supplier=supplier, order_item__in=qs)}

    # Destination suggestion for lines the supplier still has to send.
    dest_cache = {}
    for it in items:
        s = ships_all.get(it.pk)
        if s is not None:
            s.dest = _dest_suggestion(it.order, s.target, dest_cache) \
                if (s.mode == 'send' and s.status == 'confirmed') else ''

    def _needs_action(s):
        # Still to pack (awaiting), or packed but not yet dispatched.
        # Delayed and unavailable lines live in their own tabs.
        if s is None or s.status == 'awaiting':
            return True
        return s.status == 'confirmed' and s.mode == 'send'

    def _is_delayed(s):
        return s is not None and s.status == 'delayed'

    def _is_unavailable(s):
        return s is not None and s.status == 'unavailable'

    count_action = sum(1 for it in items if _needs_action(ships_all.get(it.pk)))
    count_delayed = sum(1 for it in items if _is_delayed(ships_all.get(it.pk)))
    count_unavailable = sum(1 for it in items if _is_unavailable(ships_all.get(it.pk)))
    count_all = len(items)
    count_completed = count_all - count_action - count_delayed - count_unavailable

    tab = request.GET.get('tab', 'action')
    if tab == 'action':
        visible_items = [it for it in items if _needs_action(ships_all.get(it.pk))]
    elif tab == 'delayed':
        visible_items = [it for it in items if _is_delayed(ships_all.get(it.pk))]
    elif tab == 'unavailable':
        visible_items = [it for it in items if _is_unavailable(ships_all.get(it.pk))]
    elif tab == 'completed':
        visible_items = [it for it in items
                         if not _needs_action(ships_all.get(it.pk))
                         and not _is_delayed(ships_all.get(it.pk))
                         and not _is_unavailable(ships_all.get(it.pk))]
    else:
        tab = 'all'
        visible_items = items

    # ── Packing summary (awaiting lines in the current tab + filters) ───────
    packing = {}
    for it in visible_items:
        s = ships_all.get(it.pk)
        if s is not None and s.status not in ('awaiting', 'delayed'):
            continue  # already packed / dispatched / unavailable - nothing left to pack
        size = it.variant_label or (it.variant.label if it.variant else '-')
        row = packing.setdefault((it.product.name, size), {
            'product': it.product.name, 'size': size, 'total_qty': 0,
            'orders': set(), 'value': 0,
            'stock': (it.variant.stock if it.variant else None),
        })
        row['total_qty'] += it.quantity
        row['orders'].add(it.order_id)
        row['value'] += (it.price or 0) * it.quantity
    packing_summary, pack_total_value, pack_total_qty = [], 0, 0
    for row in packing.values():
        row['order_count'] = len(row['orders'])
        del row['orders']
        row['short'] = row['stock'] is not None and row['stock'] < row['total_qty']
        row['short_by'] = max(0, row['total_qty'] - row['stock']) if row['stock'] is not None else 0
        row['value'] = int(round(row['value']))
        pack_total_value += row['value']
        pack_total_qty += row['total_qty']
        packing_summary.append(row)
    packing_summary.sort(key=lambda r: (r['product'], r['size']))

    # CSV download of the packing list (same tab + filters).
    if request.GET.get('export') == 'csv':
        resp = HttpResponse(content_type='text/csv; charset=utf-8')
        resp['Content-Disposition'] = (
            f'attachment; filename="packing-list-{timezone.localdate():%Y-%m-%d}.csv"')
        resp.write('\ufeff')  # BOM so Excel opens UTF-8 correctly
        w = csv.writer(resp)
        w.writerow(['Product', 'Pack size', 'Qty to pack', 'In stock', 'Short by',
                    'Value (INR)', 'Orders'])
        for r in packing_summary:
            w.writerow([r['product'], r['size'], r['total_qty'],
                        '' if r['stock'] is None else r['stock'],
                        r['short_by'], r['value'], r['order_count']])
        w.writerow(['TOTAL', '', pack_total_qty, '', '', pack_total_value, ''])
        return resp

    # ── Group by order -> cards ─────────────────────────────────────────────
    now = timezone.now()
    target_labels = dict(SupplierShipment.TARGET_CHOICES)
    grouped = {}
    for it in visible_items:
        grouped.setdefault(it.order, []).append((it, ships_all.get(it.pk)))

    order_groups = []
    for o, lines in grouped.items():
        send_by_target = {}
        for it, s in lines:
            # Only READY (packed) send lines can be dispatched together.
            if s and s.mode == 'send' and s.status == 'confirmed':
                send_by_target.setdefault(s.target, []).append((it, s))
        bulk_groups = [{
            'target': t,
            'label': target_labels.get(t, t),
            'count': len(v),
            'dest': v[0][1].dest,
        } for t, v in send_by_target.items() if len(v) > 1]

        targets = []
        for _it, s in lines:
            if s:
                lbl = target_labels.get(s.target, s.target)
                if lbl not in targets:
                    targets.append(lbl)

        has_awaiting = any(s is None or s.status == 'awaiting' for _it, s in lines)
        age_days = (now - o.created_at).days if has_awaiting else 0
        order_groups.append({
            'order': o,
            'lines': lines,
            'bulk_groups': bulk_groups,
            'targets': targets,
            'selectable': sum(1 for _it, s in lines
                              if s and s.status in ('awaiting', 'delayed')),
            'waiting_days': age_days,
            'overdue': age_days >= SHIP_OVERDUE_DAYS,
        })

    paginator = Paginator(order_groups, SHIP_PER_PAGE)
    page_obj = paginator.get_page(request.GET.get('page'))
    selectable_count = sum(g['selectable'] for g in page_obj.object_list)

    # Query string that carries the filters through tabs / pagination links.
    keep = {k: v for k, v in {
        'product': f_product, 'from': f_from, 'to': f_to,
        'target': f_target, 'q': f_q, 'sort': f_sort if f_sort != 'old' else '',
    }.items() if v}
    filter_qs = ('&' + urlencode(keep)) if keep else ''

    filter_products = Product.objects.filter(
        supplier=supplier, is_deleted=False).order_by('name').values('id', 'name')

    # How many filters are in use (shown on the collapsed Filters header).
    active_filter_count = sum(1 for v in (f_product, f_from, f_to, f_target, f_q) if v) \
        + (1 if f_sort != 'old' else 0)

    return render(request, 'suppliers/orders.html', {
        'active_filter_count': active_filter_count,
        'order_groups': page_obj.object_list,
        'page_obj': page_obj,
        'paginator': paginator,
        'filter_qs': filter_qs,
        'selectable_count': selectable_count,
        'tab': tab,
        'count_action': count_action,
        'count_completed': count_completed,
        'count_unavailable': count_unavailable,
        'count_delayed': count_delayed,
        'count_all': count_all,
        'delay_reasons': SupplierShipment.DELAY_REASONS,
        'unavailable_reasons': SupplierShipment.UNAVAILABLE_REASONS,
        'today': timezone.localdate(),
        'supplier': supplier,
        'global_mode_choices': Supplier.SHIPMENT_MODE_CHOICES,
        'filter_products': list(filter_products),
        'target_choices': [
            ('stockist', 'Stockist'), ('distributor', 'Distributor'),
            ('shop', 'Shop / Marketplace'), ('customer', 'Customer'),
        ],
        'sort_choices': [('old', 'Oldest first'), ('new', 'Newest first')],
        'f_product': f_product, 'f_from': f_from, 'f_to': f_to,
        'f_target': f_target, 'f_q': f_q, 'f_sort': f_sort,
        'packing_summary': packing_summary,
        'pack_total_value': pack_total_value,
        'pack_total_qty': pack_total_qty,
        'overdue_days': SHIP_OVERDUE_DAYS,
    })


@supplier_required
def price_history(request):
    """Supplier sees price history for all their variants."""
    supplier = request.user.supplier_profile
    from products.models import VariantPriceHistory
    product_id = request.GET.get('product')
    products = Product.objects.filter(supplier=supplier, status='approved')

    history = VariantPriceHistory.objects.filter(
        variant__product__supplier=supplier
    ).select_related('variant__product').order_by('-changed_at')

    if product_id:
        history = history.filter(variant__product_id=product_id)

    return render(request, 'suppliers/price_history.html', {
        'history': history,
        'products': products,
        'selected_product': product_id,
    })


@supplier_required
def bulk_image_upload(request):
    """Supplier uploads images for multiple products at once."""
    supplier = request.user.supplier_profile
    products = Product.objects.filter(supplier=supplier, is_deleted=False).order_by('name')
    if request.method == 'POST':
        updated = 0
        for product in products:
            key = f'image_{product.pk}'
            if key in request.FILES:
                product.image = request.FILES[key]
                product.save(update_fields=['image'])
                updated += 1
        messages.success(request, f'{updated} product image(s) updated.')
        return redirect('suppliers:bulk_image_upload')
    return render(request, 'suppliers/bulk_image_upload.html', {'products': products})


@supplier_required
def payout_history(request):
    """Supplier sees sold item value grouped by order delivery status."""
    supplier = request.user.supplier_profile
    sales_items = OrderItem.objects.filter(
        product__supplier=supplier
    ).select_related('order', 'product', 'variant').order_by('-order__created_at', '-id')
    for item in sales_items:
        unit_price = item.supplier_price or (item.variant.selling_price if item.variant_id else 0)
        item.supplier_value = unit_price * item.quantity
    total_earned = sum(item.supplier_value for item in sales_items)
    paid = sum(item.supplier_value for item in sales_items if item.order.status == 'delivered')
    return render(request, 'suppliers/payout_history.html', {
        'sales_items': sales_items,
        'total_earned': total_earned,
        'paid': paid,
        'pending': total_earned - paid,
    })


@supplier_required
def bulk_upload(request):
    supplier = request.user.supplier_profile
    if request.method == 'POST' and 'excel_file' in request.FILES:
        excel_file = request.FILES['excel_file']
        if not excel_file.name.endswith(('.xlsx', '.xls')):
            messages.error(request, 'Please upload a valid Excel file.')
            return redirect('suppliers:bulk_upload')
        
        try:
            import openpyxl
            wb = openpyxl.load_workbook(excel_file)
            sheet = wb.active
            
            from products.models import Product, ProductVariant, Category
            from distributors.services import mark_distributor_prices_stale
            
            # Skip header row (row 1), start from row 2
            count = 0
            for row in sheet.iter_rows(min_row=2, values_only=True):
                if not row[0]: # Skip empty rows
                    continue
                    
                # Expected columns: Category Slug, Product Name, Description, Variant Label, MRP, Selling Price, Stock
                cat_slug = str(row[0]).strip()
                name = str(row[1]).strip()
                description = str(row[2] or '').strip()
                variant_label = str(row[3]).strip()
                
                try:
                    mrp = float(row[4] or 0)
                    selling_price = float(row[5] or 0)
                    stock = int(row[6] or 0)
                except (ValueError, TypeError):
                    continue # Skip row on invalid number
                
                try:
                    category = Category.objects.get(slug=cat_slug)
                except Category.DoesNotExist:
                    continue # Skip if category not found
                
                # Create or get Product
                product, created = Product.objects.get_or_create(
                    supplier=supplier,
                    name=name,
                    category=category,
                    defaults={'description': description, 'status': 'pending'}
                )
                
                # Create Variant
                variant = ProductVariant.objects.create(
                    product=product,
                    label=variant_label,
                    mrp=mrp,
                    selling_price=selling_price,
                    stock=stock
                )
                
                # Mark all existing distributor prices as stale for this variant
                # This prevents bulk uploads from bypassing the stale-flag lifecycle
                try:
                    mark_distributor_prices_stale(variant.pk, variant.selling_price)
                except Exception:
                    pass  # Silently continue if service fails
                
                count += 1
                
            messages.success(request, f'Successfully imported {count} product variants!')
            return redirect('suppliers:product_list')
            
        except Exception as e:
            messages.error(request, f'Error processing file: {str(e)}')
            return redirect('suppliers:bulk_upload')
            
    return render(request, 'suppliers/bulk_upload.html')


@supplier_required
def download_bulk_template(request):
    import openpyxl
    from django.http import HttpResponse
    
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Bulk Upload Template"
    
    headers = ['Category Slug', 'Product Name', 'Description', 'Variant Label (e.g. 1kg)', 'MRP', 'Selling Price', 'Initial Stock']
    ws.append(headers)
    ws.append(['dry-fruits', 'Premium Almonds', 'High quality almonds', '500g', 800, 650, 50])
    
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="lakrish_bulk_template.xlsx"'
    wb.save(response)
    return response


@supplier_required
def toggle_pricing_mode(request, pk):
    from django.http import JsonResponse
    supplier = request.user.supplier_profile
    product = get_object_or_404(Product, pk=pk, supplier=supplier)
    if request.method == 'POST':
        new_mode = 'manual' if product.pricing_mode == 'auto' else 'auto'
        product.pricing_mode = new_mode
        product.save(update_fields=['pricing_mode'])
        # If switching to auto, recalculate prices from master
        if new_mode == 'auto' and product.master_stock_grams:
            product.sync_variant_stocks()
        return JsonResponse({'mode': new_mode})
    return JsonResponse({'error': 'POST required'}, status=405)


@supplier_required
def storefront(request):
    """Supplier sees a customer-style view of only their own approved products."""
    supplier = request.user.supplier_profile
    products = Product.objects.filter(
        supplier=supplier, status='approved', is_deleted=False
    ).prefetch_related('variants').order_by('name')
    return render(request, 'suppliers/storefront.html', {
        'supplier': supplier,
        'products': products,
    })


@supplier_required
def search_images(request):
    """Search product images using DuckDuckGo + Wikimedia fallback. No API key needed."""
    from django.http import JsonResponse
    import urllib.request, urllib.parse, json as _json, re

    q = request.GET.get('q', '').strip()
    if not q:
        return JsonResponse([], safe=False)

    results = []
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
    }

    # 1. DuckDuckGo image search (unofficial, no key needed)
    try:
        # First get vqd token
        search_url = f'https://duckduckgo.com/?q={urllib.parse.quote(q)}&iax=images&ia=images'
        req = urllib.request.Request(search_url, headers=headers)
        with urllib.request.urlopen(req, timeout=6) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
        vqd_match = re.search(r'vqd=(["\'])([^"\']+)\1', html)
        if not vqd_match:
            vqd_match = re.search(r'vqd=([\d-]+)', html)
        if vqd_match:
            vqd = vqd_match.group(2) if vqd_match.lastindex >= 2 else vqd_match.group(1)
            img_params = urllib.parse.urlencode({
                'l': 'us-en', 'o': 'json', 'q': q, 'vqd': vqd,
                'f': ',,,,,', 'p': '1',
            })
            img_url = f'https://duckduckgo.com/i.js?{img_params}'
            req2 = urllib.request.Request(img_url, headers={**headers, 'Referer': 'https://duckduckgo.com/'})
            with urllib.request.urlopen(req2, timeout=6) as resp2:
                data = _json.loads(resp2.read())
            for item in data.get('results', [])[:8]:
                thumb = item.get('thumbnail') or item.get('image', '')
                full  = item.get('image', thumb)
                title = item.get('title', q)
                if thumb:
                    results.append({'url': full, 'thumb': thumb, 'title': title})
    except Exception:
        pass

    # 2. Fallback: Wikimedia Commons
    if not results:
        try:
            params = urllib.parse.urlencode({
                'action': 'query', 'generator': 'search',
                'gsrnamespace': 6, 'gsrsearch': f'File:{q} food',
                'gsrlimit': 8, 'prop': 'imageinfo',
                'iiprop': 'url', 'iiurlwidth': 300,
                'format': 'json', 'origin': '*',
            })
            req = urllib.request.Request(
                f'https://commons.wikimedia.org/w/api.php?{params}',
                headers={'User-Agent': 'LaKrishMart/1.0'}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = _json.loads(resp.read())
            for page in data.get('query', {}).get('pages', {}).values():
                info = page.get('imageinfo', [{}])[0]
                url  = info.get('url', '')
                if url and any(url.lower().endswith(ext) for ext in ('.jpg', '.jpeg', '.png', '.webp')):
                    results.append({
                        'url': url, 'thumb': url,
                        'title': page.get('title', '').replace('File:', ''),
                    })
        except Exception:
            pass

    return JsonResponse(results[:8], safe=False)


@supplier_required
def restock_requests(request):
    """Supplier sees restock requests from distributors for their products."""
    from django.urls import reverse
    from distributors.models import RestockRequest
    from stockist.models import StockistRestockRequest, StockistInventory
    supplier = request.user.supplier_profile
    # 'all' is its own value: an absent ?status= means the default (pending), so the
    # "All" tab used to show Pending. Unknown values fall back to pending.
    status_filter = request.GET.get('status', 'pending')
    if status_filter != 'all' and status_filter not in {k for k, _ in RestockRequest.STATUS_CHOICES}:
        status_filter = 'pending'

    # ── Stage 7: stockist restock requests are fulfilled BY THE SUPPLIER ──────
    # This is the only way a stockist's inventory ever increases: goods move
    # supplier → stockist, so supplier stock goes down and stockist stock up.
    if request.method == 'POST' and request.POST.get('kind') == 'stockist':
        from django.utils import timezone
        req_id = request.POST.get('req_id')
        action = request.POST.get('action')
        try:
            sreq = StockistRestockRequest.objects.select_related('variant', 'stockist').get(
                pk=req_id, variant__product__supplier=supplier, status='pending'
            )
        except StockistRestockRequest.DoesNotExist:
            messages.error(request, 'Request not found.')
            return redirect('suppliers:restock_requests')
        if action == 'fulfil':
            variant = sreq.variant
            if variant.stock < sreq.quantity_requested:
                messages.error(
                    request,
                    f'You only have {variant.stock} × {variant.label} in stock — '
                    f'add stock to your product first or reject the request.'
                )
                return redirect('suppliers:restock_requests')
            from products.models import ProductVariant as PV
            PV.objects.filter(pk=variant.pk).update(stock=variant.stock - sreq.quantity_requested)
            inv, _ = StockistInventory.objects.get_or_create(
                stockist=sreq.stockist, variant=variant, defaults={'quantity': 0}
            )
            inv.quantity += sreq.quantity_requested
            inv.save()
            sreq.status = 'fulfilled'
            sreq.fulfilled_at = timezone.now()
            sreq.save()
            messages.success(
                request,
                f'{sreq.quantity_requested}× {variant.label} of {variant.product.name} '
                f'sent to {sreq.stockist.company_name}.'
            )
        elif action == 'reject':
            sreq.status = 'rejected'
            sreq.save()
            messages.warning(request, f'Stockist request #{req_id} rejected.')
        return redirect('suppliers:restock_requests')

    requests_qs = RestockRequest.objects.filter(
        variant__product__supplier=supplier
    ).select_related(
        'variant__product', 'distributor'
    ).order_by('-requested_at')

    if status_filter != 'all':
        requests_qs = requests_qs.filter(status=status_filter)

    if request.method == 'POST':
        req_id = request.POST.get('req_id')
        action = request.POST.get('action')
        try:
            r = RestockRequest.objects.select_related(
                'variant__product', 'distributor'
            ).get(pk=req_id, variant__product__supplier=supplier)
            if action == 'deliver_direct':
                # Stage 7: DIRECT-supply products are delivered by the supplier
                # straight to the shop — no stockist, no Distributor, no
                # commissions. Marketplace stock rises; supplier stock falls.
                if not r.variant.product.direct_supply:
                    messages.error(request, 'Only direct-supply products can be delivered by the supplier.')
                    return redirect('suppliers:restock_requests')
                if r.status not in ('pending', 'approved', 'in_transit'):
                    messages.error(request, 'Request already resolved.')
                    return redirect('suppliers:restock_requests')
                from distributors.models import (DistributorStock, DistributorStockBatch,
                                                 DistributorLooseStock)
                variant = r.variant
                if r.loose_grams:
                    ls, _ = DistributorLooseStock.objects.get_or_create(
                        distributor=r.distributor, product=variant.product,
                        defaults={'quantity_grams': 0, 'cost_price_per_gram': 0},
                    )
                    ls.quantity_grams += r.loose_grams
                    if variant.unit_grams:
                        ls.cost_price_per_gram = variant.selling_price / variant.unit_grams
                    ls.save()
                    delivered_what = f'{r.loose_grams}g loose {variant.product.name}'
                else:
                    if variant.stock < r.quantity_requested:
                        messages.error(
                            request,
                            f'You only have {variant.stock} × {variant.label} in stock.'
                        )
                        return redirect('suppliers:restock_requests')
                    ProductVariant.objects.filter(pk=variant.pk).update(
                        stock=variant.stock - r.quantity_requested
                    )
                    stock_obj, _ = DistributorStock.objects.get_or_create(
                        distributor=r.distributor, variant=variant, defaults={'quantity': 0}
                    )
                    DistributorStockBatch.objects.create(
                        stock=stock_obj,
                        quantity=r.quantity_requested,
                        cost_price=variant.selling_price,
                        note=f'Direct supply — request #{r.pk} delivered by supplier',
                    )
                    stock_obj.refresh_quantity()
                    delivered_what = f'{r.quantity_requested}× {variant.label} of {variant.product.name}'
                r.status = 'delivered'
                r.save()
                messages.success(request, f'{delivered_what} delivered to {r.distributor.company_name}.')
            elif action == 'dispatch':
                # Only an open request can be dispatched. Direct-supply products
                # are delivered with "Deliver (direct)" instead.
                if r.status not in ('pending', 'approved') or r.variant.product.direct_supply:
                    messages.error(
                        request,
                        f'Request #{req_id} is {r.get_status_display().lower()} and cannot be dispatched.')
                    return redirect(f"{reverse('suppliers:restock_requests')}?status={status_filter}")
                tracking = request.POST.get('tracking_info', '').strip()[:200]
                r.status = 'in_transit'
                r.tracking_info = tracking
                r.save()
                messages.success(request, f'Request #{req_id} marked as dispatched. Tracking: {tracking or "—"}')
            elif action == 'reject':
                # Never reject something already on its way or delivered: the
                # shop's stock may already have been updated.
                if r.status not in ('pending', 'approved'):
                    messages.error(
                        request,
                        f'Request #{req_id} is {r.get_status_display().lower()} and can no longer be rejected.')
                    return redirect(f"{reverse('suppliers:restock_requests')}?status={status_filter}")
                r.status = 'rejected'
                r.save()
                messages.warning(request, f'Request #{req_id} rejected.')
        except RestockRequest.DoesNotExist:
            messages.error(request, 'Request not found.')
        return redirect(f"{reverse('suppliers:restock_requests')}?status={status_filter}")

    pending_count = RestockRequest.objects.filter(
        variant__product__supplier=supplier, status='pending'
    ).count()

    stockist_requests = StockistRestockRequest.objects.filter(
        variant__product__supplier=supplier
    ).select_related('variant__product', 'stockist').order_by('-requested_at')[:50]

    return render(request, 'suppliers/restock_requests.html', {
        'requests': requests_qs,
        'open_statuses': ('pending', 'approved'),
        'stockist_requests': stockist_requests,
        'status_filter': status_filter,
        'status_choices': RestockRequest.STATUS_CHOICES,
        'pending_count': pending_count,
    })


@supplier_required
def procurement_accounts(request):
    """Supplier sees all stockist procurement accounts — how much each owes."""
    from decimal import Decimal
    from django.db.models import Sum
    from stockist.procurement_models import StockistSupplierAccount

    supplier = request.user.supplier_profile
    accounts = StockistSupplierAccount.objects.filter(
        supplier=supplier
    ).select_related('stockist').order_by('stockist__company_name')

    total_receivable = accounts.aggregate(t=Sum('balance_due'))['t'] or Decimal('0')

    return render(request, 'suppliers/procurement_accounts.html', {
        'supplier': supplier,
        'accounts': accounts,
        'total_receivable': total_receivable,
    })
