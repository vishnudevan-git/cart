from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import JsonResponse
from django.db import models
from django.db.models import Sum, Count, Q, Prefetch
from functools import wraps
from accounts.decorators import ensure_user_role
from lakrish_mart.ui_config import DEFAULT_THEME_COLOR
from products.models import Product, ProductVariant
from orders.models import Order, OrderItem
from payments.models import sync_order_ledger, sync_order_settlements
from .models import Distributor, DistributorProduct, DistributorVariantPrice


# ── decorator ────────────────────────────────────────────────────────────────

ALLOWED_BEFORE_GLOBAL_PRICING_SETUP = {
    'product_catalogue',
    'apply_global_pricing',
    'bulk_apply_global_pricing',
    # A shop's OWN products have no supply chain behind them — no supplier cost,
    # no stockist charge, no wholesale margin — so Global Pricing has nothing to
    # say about them. Gating them behind it would lock a brand-new shop out of
    # the one catalogue it can fill on day one.
    'own_product_list',
    'own_product_form',
    'own_product_toggle',
    'own_product_delete',
}


def _global_pricing_is_configured(config):
    if not isinstance(config, dict):
        return False
    for key in ('weight', 'volume', 'pieces'):
        row = config.get(key) or {}
        val = str(row.get('value', '')).strip()
        if val != '':
            return True
    return False


def _get_global_pricing_config(request, dist):
    session_config = request.session.get('global_pricing', {})
    if _global_pricing_is_configured(session_config):
        return session_config

    return {}

def distributor_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        denial = ensure_user_role(
            request,
            ('distributor',),
            set_active_role='distributor',
        )
        if denial:
            return denial
        try:
            dist = request.user.distributor_profile
            if dist.status != 'active':
                return render(request, 'distributors/suspended.html', {'distributor': dist})
        except Distributor.DoesNotExist:
            messages.error(request, 'Your distributor profile is missing. Please contact admin to create it.')
            request.session['active_role'] = 'customer'
            return redirect('products:home')

        global_cfg = _get_global_pricing_config(request, dist)
        if not _global_pricing_is_configured(global_cfg):
            # A distributor who has already priced their catalogue is considered
            # set up — don't re-gate them every new session (config is session-only).
            has_prices = DistributorVariantPrice.objects.filter(
                distributor=dist, is_active=True
            ).exists()
            if not has_prices and view_func.__name__ not in ALLOWED_BEFORE_GLOBAL_PRICING_SETUP:
                messages.warning(
                    request,
                    'Set Global Pricing first. Until then, distributor pages are locked.'
                )
                return redirect('distributors:product_catalogue')

        return view_func(request, *args, **kwargs)
    return wrapper


def _distributor_visible_products(dist):
    """Products this distributor is allowed to manage in catalogue flows."""
    own_listed_ids = DistributorProduct.objects.filter(
        distributor=dist, is_listed=True
    ).values_list('product_id', flat=True)
    # all_objects + visible_to_enterprise: the shared marketplace PLUS this
    # shop's own private products — never another shop's own products.
    qs = Product.all_objects.visible_to_enterprise(dist).filter(
        Q(status='approved') |
        Q(status='pending', charges_confirmed=True, pk__in=own_listed_ids),
        is_deleted=False,
    )
    return qs


# ── dashboard ─────────────────────────────────────────────────────────────────

@distributor_required
def dashboard(request):
    dist = request.user.distributor_profile
    total_orders = Order.objects.filter(distributor=dist).count()
    pending_orders = Order.objects.filter(distributor=dist, status='pending').count()
    total_customers = dist.customers.count()
    total_revenue = OrderItem.objects.filter(
        order__distributor=dist, order__status='delivered'
    ).aggregate(t=Sum('price'))['t'] or 0
    recent_orders = Order.objects.filter(distributor=dist).select_related('customer').order_by('-created_at')[:10]
    return render(request, 'distributors/dashboard.html', {
        'dist': dist,
        'total_orders': total_orders,
        'pending_orders': pending_orders,
        'total_customers': total_customers,
        'total_revenue': total_revenue,
        'recent_orders': recent_orders,
    })


# ── product catalogue management ─────────────────────────────────────────────

@distributor_required
def product_catalogue(request):
    """Distributor sees all admin-approved products and can toggle listing + set prices."""
    dist = request.user.distributor_profile
    supplier_filter = request.GET.get('supplier', '')
    status_filter = request.GET.get('status', '')  # listed / unlisted / all
    category_filter = request.GET.get('category', '')

    approved_products = list(_distributor_visible_products(dist).prefetch_related(
        'variants', 'dist_products', 'category'
    ).select_related('supplier').order_by('supplier__company_name', 'name'))

    if supplier_filter:
        approved_products = [p for p in approved_products if str(p.supplier_id) == supplier_filter]
    if category_filter:
        approved_products = [p for p in approved_products if str(p.category_id) == category_filter]

    from suppliers.models import Supplier
    suppliers = Supplier.objects.filter(status='approved').order_by('company_name')
    from products.models import Category
    categories = Category.objects.all().order_by('name')

    # Build a map of existing DistributorProduct entries
    dp_map = {dp.product_id: dp for dp in DistributorProduct.objects.filter(distributor=dist)}
    dvp_map = {dvp.variant_id: dvp for dvp in DistributorVariantPrice.objects.filter(distributor=dist)}

    # Get active stockist for price calculations
    from stockist.models import get_stockist_price, stockist_for, Stockist as StockistModel
    active_stockist = stockist_for(dist)

    # Build name→suppliers map for duplicate info display only (no restriction)
    from collections import defaultdict, OrderedDict
    name_to_suppliers = defaultdict(list)
    for p in approved_products:
        name = p.name.lower()
        name_to_suppliers[name].append((p.pk, p.supplier.company_name))

    products_data = []
    for p in approved_products:
        dp = dp_map.get(p.pk)
        name = p.name.lower()
        is_listed = bool(dp and dp.is_listed)

        duplicate_suppliers = [
            sname for pid, sname in name_to_suppliers[name] if pid != p.pk
        ]

        variants_data = []
        for v in p.variants.filter(is_active=True):
            dvp = dvp_map.get(v.pk)
            # Get stockist price = supplier price + all active charges
            stockist_price, charges_breakdown = get_stockist_price(p, v, active_stockist, enterprise=dist)
            # Use stockist price as the admin/cost price for distributor
            base_price = dvp.base_price if dvp else stockist_price
            selling_price = dvp.selling_price if dvp else stockist_price
            variants_data.append({
                'variant': v,
                'dvp': dvp,
                'supplier_price': v.selling_price,
                'stockist_price': stockist_price,
                'charges_breakdown': charges_breakdown,
                'base_price': base_price,
                'selling_price': selling_price,
                'price_stale': dvp.price_stale if dvp else False,
                'new_supplier_price': dvp.new_supplier_price if dvp else None,
            })
        products_data.append({
            'product': p,
            'dp': dp,
            'is_listed': is_listed,
            'charges_pending': not p.charges_confirmed and not p.direct_supply,
            'variants': variants_data,
            'has_duplicate': bool(duplicate_suppliers),
            'duplicate_suppliers': duplicate_suppliers,
        })

    # Counts always from full unfiltered data
    listed_count = sum(1 for pd in products_data if pd['is_listed'])
    unlisted_count = sum(1 for pd in products_data if not pd['is_listed'])

    def _is_manual(pd):
        if not pd['is_listed'] or not pd['dp']:
            return False
        return bool(getattr(pd['dp'], 'margin_is_manual', False))

    global_price_count = sum(1 for pd in products_data if pd['is_listed'] and pd['dp'] and not _is_manual(pd))
    manual_price_count = sum(1 for pd in products_data if pd['is_listed'] and _is_manual(pd))

    # Apply status filter after counting
    if status_filter == 'listed':
        products_data = [pd for pd in products_data if pd['is_listed']]
    elif status_filter == 'unlisted':
        products_data = [pd for pd in products_data if not pd['is_listed']]
    elif status_filter == 'global_price':
        products_data = [pd for pd in products_data if pd['is_listed'] and pd['dp'] and not _is_manual(pd)]
    elif status_filter == 'manual_price':
        products_data = [pd for pd in products_data if pd['is_listed'] and _is_manual(pd)]

    # Group products by name so duplicates appear side by side
    from collections import OrderedDict
    grouped = OrderedDict()
    for pd in products_data:
        name = pd['product'].name.lower()
        if name not in grouped:
            grouped[name] = []
        grouped[name].append(pd)
    grouped_products = list(grouped.values())

    global_pricing_config = _get_global_pricing_config(request, dist)

    return render(request, 'distributors/product_catalogue.html', {
        'dist': dist,
        'products_data': products_data,
        'grouped_products': grouped_products,
        'suppliers': suppliers,
        'categories': categories,
        'supplier_filter': supplier_filter,
        'category_filter': category_filter,
        'status_filter': status_filter,
        'listed_count': listed_count,
        'unlisted_count': unlisted_count,
        'global_price_count': global_price_count,
        'manual_price_count': manual_price_count,
        'global_pricing': global_pricing_config,
        'require_global_pricing_setup': not _global_pricing_is_configured(global_pricing_config),
    })


@distributor_required
def toggle_auto_list(request):
    """Compatibility endpoint used by older UI to toggle auto-listing settings."""
    if request.method != 'POST':
        return redirect('distributors:product_catalogue')

    dist = request.user.distributor_profile
    val = request.POST.get('auto_list_new_products')
    if val is None:
        dist.auto_list_new_products = not dist.auto_list_new_products
    else:
        dist.auto_list_new_products = str(val).lower() in ('1', 'true', 'yes', 'on')
    dist.save(update_fields=['auto_list_new_products'])

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'ok': True, 'auto_list_new_products': dist.auto_list_new_products})

    messages.success(request, 'Auto-list setting updated.')
    return redirect('distributors:product_catalogue')


@distributor_required
def toggle_hide_out_of_stock(request):
    """Distributor can choose whether customers should see out-of-stock products."""
    if request.method != 'POST':
        return redirect('distributors:dashboard')

    dist = request.user.distributor_profile
    val = request.POST.get('hide_out_of_stock_products')
    if val is None:
        dist.hide_out_of_stock_products = not dist.hide_out_of_stock_products
    else:
        dist.hide_out_of_stock_products = str(val).lower() in ('1', 'true', 'yes', 'on')
    dist.save(update_fields=['hide_out_of_stock_products'])

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'ok': True, 'hide_out_of_stock_products': dist.hide_out_of_stock_products})

    messages.success(request, 'Customer stock visibility setting updated.')
    return redirect('distributors:dashboard')


@distributor_required
def bulk_list_all(request):
    """Compatibility endpoint to list all visible products for distributor."""
    if request.method != 'POST':
        return redirect('distributors:product_catalogue')

    from decimal import Decimal
    import re as regex
    from stockist.models import get_stockist_price, stockist_for, Stockist as StockistModel
    from distributors.services import apply_max_percent_cap

    dist = request.user.distributor_profile
    products = _distributor_visible_products(dist)
    active_stockist = stockist_for(dist)

    mode = dist.auto_list_margin_mode or 'percent'
    rate = Decimal(str(dist.auto_list_margin_value or 0))
    cap_percent = dist.auto_list_margin_cap_percent

    count = 0
    variant_updates = 0
    for product in products:
        dp, _ = DistributorProduct.objects.get_or_create(distributor=dist, product=product)
        if not dp.margin_is_manual:
            dp.margin_mode = mode
            dp.margin_percent = rate
        if not dp.is_listed:
            dp.is_listed = True
            count += 1
        update_fields = ['is_listed']
        if not dp.margin_is_manual:
            update_fields.extend(['margin_mode', 'margin_percent'])
        dp.save(update_fields=update_fields)

        for variant in product.variants.filter(is_active=True):
            stockist_price, _ = get_stockist_price(product, variant, active_stockist, enterprise=dist)
            base_price = stockist_price or variant.selling_price

            mode_to_use = dp.margin_mode if dp.margin_is_manual else mode
            rate_to_use = Decimal(str(dp.margin_percent if dp.margin_is_manual and dp.margin_percent is not None else rate))

            if mode_to_use == 'per_unit':
                label = variant.label.strip().lower()
                m = regex.match(r'^([\d.]+)\s*(kg|g|ml|l|litre|liter)?$', label)
                if m:
                    num = float(m.group(1))
                    unit = (m.group(2) or 'g').lower()
                    if unit in ('kg', 'l', 'litre', 'liter'):
                        multiplier = num
                    else:
                        multiplier = num / 1000
                else:
                    multiplier = 1.0
                markup = float(rate_to_use) * multiplier
                sell = base_price + Decimal(str(round(markup, 2)))
                sell = apply_max_percent_cap(base_price, sell, cap_percent)
            else:
                sell = base_price * (1 + rate_to_use / 100)

            if product.show_mrp and variant.mrp:
                sell = min(sell, variant.mrp)
            sell = max(sell, base_price)

            DistributorVariantPrice.objects.update_or_create(
                distributor=dist,
                variant=variant,
                defaults={
                    'base_price': base_price,
                    'selling_price': sell,
                    'is_active': True,
                    'price_stale': False,
                },
            )
            variant_updates += 1

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'ok': True, 'updated': count, 'variant_updates': variant_updates})

    messages.success(request, f'Listed {count} product(s) and applied default margin to {variant_updates} variant price(s).')
    return redirect('distributors:product_catalogue')


@distributor_required
def bulk_apply_global_pricing(request):
    """Backward-compatible alias for global pricing endpoint."""
    return apply_global_pricing(request)


@distributor_required
def bulk_toggle_listing(request):
    """Toggle listing state for selected products in one request."""
    if request.method != 'POST':
        return redirect('distributors:product_catalogue')

    dist = request.user.distributor_profile
    content_type = (request.headers.get('content-type') or '').lower()
    accepts_json = 'application/json' in (request.headers.get('accept') or '').lower()
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest' or 'application/json' in content_type or accepts_json

    payload = {}
    if 'application/json' in content_type:
        import json
        try:
            payload = json.loads(request.body.decode('utf-8') or '{}')
        except Exception:
            payload = {}

    product_ids = request.POST.getlist('product_ids')
    if not product_ids:
        product_ids = payload.get('product_pks') or payload.get('product_ids') or []

    if isinstance(product_ids, str):
        product_ids = [p.strip() for p in product_ids.split(',') if p.strip()]

    action = (payload.get('action') or '').strip().lower()
    if action in ('list', 'unlist'):
        is_listed = action == 'list'
    else:
        list_state = request.POST.get('is_listed', request.POST.get('listed', payload.get('is_listed', '1')))
        is_listed = str(list_state).lower() in ('1', 'true', 'yes', 'on')

    if not product_ids:
        if is_ajax:
            return JsonResponse({'success': False, 'ok': False, 'error': 'No products selected.'}, status=400)
        messages.warning(request, 'No products selected.')
        return redirect('distributors:product_catalogue')

    visible_ids = set(_distributor_visible_products(dist).values_list('id', flat=True))
    updated = 0
    updated_pks = []
    for pid in product_ids:
        try:
            product_id = int(pid)
        except (TypeError, ValueError):
            continue
        if product_id not in visible_ids:
            continue
        dp, _ = DistributorProduct.objects.get_or_create(distributor=dist, product_id=product_id)
        if dp.is_listed != is_listed:
            dp.is_listed = is_listed
            dp.save(update_fields=['is_listed'])
            updated += 1
        updated_pks.append(product_id)

    if is_ajax:
        message = f'Updated listing state for {updated} product(s).'
        return JsonResponse({
            'success': True,
            'ok': True,
            'updated': updated,
            'updated_pks': updated_pks,
            'is_listed': is_listed,
            'message': message,
        })

    messages.success(request, f'Updated listing state for {updated} product(s).')
    return redirect('distributors:product_catalogue')


@distributor_required
def save_product_listing(request, product_pk):
    """Toggle listing and save prices for a product."""
    if request.method != 'POST':
        return redirect('distributors:product_catalogue')

    dist = request.user.distributor_profile
    product = get_object_or_404(_distributor_visible_products(dist), pk=product_pk)

    is_listed = request.POST.get('is_listed') == '1'
    margin = request.POST.get('margin_percent', '').strip()
    margin_mode = request.POST.get('margin_mode', 'percent')
    margin_is_manual = margin != ''

    # Read global pricing config from session for default margin fallback.
    global_pricing_session = _get_global_pricing_config(request, dist)
    product_master_unit = getattr(product, 'master_unit', 'g')
    if product_master_unit in ('g',):
        unit_key = 'weight'
    elif product_master_unit in ('ml',):
        unit_key = 'volume'
    else:
        unit_key = 'pieces'
    global_unit_config = global_pricing_session.get(unit_key, {})

    # If no product-level margin is sent, default to global margin for this unit type.
    if margin == '' and global_unit_config.get('value') not in (None, ''):
        margin = str(global_unit_config.get('value')).strip()
        margin_mode = str(global_unit_config.get('mode') or 'percent').strip().lower()
        if margin_mode not in ('percent', 'per_unit'):
            margin_mode = 'percent'

    # Fallback to distributor default global margin when session global is not set.
    if margin == '':
        margin = str(dist.auto_list_margin_value or 0)
        margin_mode = str(dist.auto_list_margin_mode or 'percent').strip().lower()
        if margin_mode not in ('percent', 'per_unit'):
            margin_mode = 'percent'

    dp, _ = DistributorProduct.objects.get_or_create(
        distributor=dist, product=product
    )
    dp.is_listed = is_listed
    dp.margin_percent = float(margin) if margin != '' else None
    dp.margin_mode = margin_mode
    dp.margin_is_manual = margin_is_manual
    dp.save()

    from decimal import Decimal
    import re as regex
    from distributors.services import apply_max_percent_cap

    # Apply global cap (if configured) for this unit type.
    global_cap_percent = None
    if global_unit_config.get('cap'):
        try:
            global_cap_percent = Decimal(str(global_unit_config.get('cap')))
        except Exception:
            pass

    from stockist.models import get_stockist_price, stockist_for, Stockist as StockistModel
    active_stockist = stockist_for(dist)

    for variant in product.variants.filter(is_active=True):
        selling_price_key = f'selling_price_{variant.pk}'
        selling_price_raw = request.POST.get(selling_price_key, '').strip()

        # Get current stockist floor price
        stockist_price, _ = get_stockist_price(product, variant, active_stockist, enterprise=dist)
        current_floor = stockist_price or variant.selling_price
        base_price = current_floor

        existing_dvp = DistributorVariantPrice.objects.filter(
            distributor=dist, variant=variant
        ).first()
        if existing_dvp and existing_dvp.base_price:
            base_price = max(existing_dvp.base_price, current_floor)

        # If user typed a specific price, use it
        if selling_price_raw:
            sell = Decimal(selling_price_raw)
        elif margin:
            # Recalculate from margin
            margin_val = Decimal(margin)
            if margin_mode == 'per_unit':
                label = variant.label.strip().lower()
                m = regex.match(r'^([\d.]+)\s*(kg|g|ml|l|litre|liter)?$', label)
                if m:
                    num = float(m.group(1))
                    unit = (m.group(2) or 'g').lower()
                    if unit in ('kg', 'l', 'litre', 'liter'):
                        multiplier = num
                    else:
                        multiplier = num / 1000
                else:
                    multiplier = 1.0
                markup = float(margin_val) * multiplier
                sell = base_price + Decimal(str(round(markup, 2)))
                # Apply global unit cap if available
                if global_cap_percent is not None and global_cap_percent > 0:
                    sell = Decimal(str(apply_max_percent_cap(base_price, sell, global_cap_percent)))
            else:
                sell = base_price * (1 + margin_val / 100)
        else:
            # No price and no margin — keep existing or use base
            sell = existing_dvp.selling_price if existing_dvp else base_price

        # Enforce MRP cap
        if product.show_mrp and variant.mrp:
            sell = min(sell, variant.mrp)
        sell = max(sell, base_price)

        # Round to whole number for storage
        sell_rounded = Decimal(str(int(round(float(sell)))))

        DistributorVariantPrice.objects.update_or_create(
            distributor=dist, variant=variant,
            defaults={
                'base_price': base_price,
                'selling_price': sell_rounded,
                'is_active': is_listed,
                'price_stale': False,
                'new_supplier_price': None,
            }
        )

    messages.success(request, f'"{product.name}" {"listed" if is_listed else "unlisted"} — prices updated.')
    return redirect('distributors:product_catalogue')


# ── orders ────────────────────────────────────────────────────────────────────

@distributor_required
def apply_global_pricing(request):
    """Apply margin by unit type (weight/volume/pieces) to all products."""
    if request.method != 'POST':
        return redirect('distributors:product_catalogue')

    from decimal import Decimal, InvalidOperation
    import re as regex
    from distributors.services import apply_max_percent_cap
    import json
    dist = request.user.distributor_profile

    content_type = (request.headers.get('content-type') or '').lower()
    accepts_json = 'application/json' in (request.headers.get('accept') or '').lower()
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest' or 'application/json' in content_type or accepts_json

    payload = {}
    if 'application/json' in content_type:
        try:
            payload = json.loads(request.body.decode('utf-8') or '{}')
        except Exception:
            payload = {}

    def get_req_value(key, default=''):
        if key in request.POST:
            return request.POST.get(key, default)
        return payload.get(key, default)

    # Parse per-unit-type margins
    unit_configs = {}
    for unit_key, unit_label in [('weight', 'g'), ('volume', 'ml'), ('pieces', 'qty')]:
        mode = get_req_value(f'mode_{unit_key}', 'percent')
        val_raw = str(get_req_value(f'value_{unit_key}', '')).strip()
        cap_raw = str(get_req_value(f'cap_{unit_key}', '')).strip()
        if not val_raw:
            continue
        try:
            val = Decimal(val_raw)
        except InvalidOperation:
            continue
        cap = None
        if cap_raw:
            try:
                cap = Decimal(cap_raw)
            except InvalidOperation:
                cap = None
        if mode not in ('percent', 'per_unit'):
            mode = 'percent'
        if mode == 'per_unit' and (cap is None or cap <= 0):
            cap = Decimal('10')
        if mode != 'per_unit':
            cap = None
        unit_configs[unit_key] = {'mode': mode, 'value': val, 'cap': cap}

    if not unit_configs:
        if is_ajax:
            return JsonResponse({'success': False, 'ok': False, 'error': 'Please enter a margin value for at least one unit type.'}, status=400)
        messages.warning(request, 'Please enter a margin value for at least one unit type.')
        return redirect('distributors:product_catalogue')

    product_ids = payload.get('product_pks') or payload.get('product_ids') or []
    if isinstance(product_ids, str):
        product_ids = [p.strip() for p in product_ids.split(',') if p.strip()]

    from django.db.models import Q as _Q
    base_products_qs = Product.objects.filter(
        _Q(charges_confirmed=True) | _Q(direct_supply=True), status='approved'
    )

    if product_ids:
        try:
            product_ids = [int(pid) for pid in product_ids]
        except (TypeError, ValueError):
            product_ids = []
        visible_ids = set(_distributor_visible_products(dist).values_list('id', flat=True))
        product_ids = [pid for pid in product_ids if pid in visible_ids]
        base_products_qs = base_products_qs.filter(pk__in=product_ids)

    products = list(base_products_qs.prefetch_related('variants').select_related('supplier'))

    if not products:
        if is_ajax:
            return JsonResponse({'success': False, 'ok': False, 'error': 'No eligible products found for pricing update.'}, status=400)
        messages.warning(request, 'No eligible products found for pricing update.')
        return redirect('distributors:product_catalogue')

    updated_products = 0
    updated_variants = 0
    variant_prices = {}
    from stockist.models import get_stockist_price, stockist_for, Stockist as StockistModel
    active_stockist = stockist_for(dist)

    for p in products:
        # Determine product's unit type
        master_unit = getattr(p, 'master_unit', 'g')
        if master_unit in ('g',):
            unit_key = 'weight'
        elif master_unit in ('ml',):
            unit_key = 'volume'
        else:
            unit_key = 'pieces'

        config = unit_configs.get(unit_key)
        if not config:
            continue

        margin_mode = config['mode']
        margin_val = config['value']

        dp, _ = DistributorProduct.objects.get_or_create(distributor=dist, product=p)
        dp.margin_mode = margin_mode
        dp.margin_percent = margin_val
        dp.margin_is_manual = False
        dp.save()
        updated_products += 1

        for v in p.variants.filter(is_active=True):
            # Get current stockist floor price
            stockist_price, _ = get_stockist_price(p, v, active_stockist, enterprise=dist)
            current_floor = stockist_price or v.selling_price
            base_price = current_floor

            existing_dvp = DistributorVariantPrice.objects.filter(
                distributor=dist, variant=v
            ).first()
            if existing_dvp and existing_dvp.base_price:
                base_price = max(existing_dvp.base_price, current_floor)

            # Calculate selling price
            if margin_mode == 'per_unit':
                label = v.label.strip().lower()
                m = regex.match(r'^([\d.]+)\s*(kg|g|ml|l|litre|liter)?$', label)
                if m:
                    num = float(m.group(1))
                    unit = (m.group(2) or 'g').lower()
                    if unit in ('kg', 'l', 'litre', 'liter'):
                        multiplier = num
                    else:
                        multiplier = num / 1000
                else:
                    multiplier = 1.0
                markup = float(margin_val) * multiplier
                sell = base_price + Decimal(str(round(markup, 2)))
                cap_pct = config.get('cap')
                sell = apply_max_percent_cap(base_price, sell, cap_pct)
            else:
                sell = base_price * (1 + margin_val / 100)

            # Enforce MRP cap
            if p.show_mrp and v.mrp:
                sell = min(sell, v.mrp)
            sell = max(sell, base_price)

            # Round to whole number for storage
            sell_rounded = Decimal(str(int(round(float(sell)))))

            DistributorVariantPrice.objects.update_or_create(
                distributor=dist, variant=v,
                defaults={
                    'base_price': base_price,
                    'selling_price': sell_rounded,
                    'is_active': dp.is_listed,
                }
            )
            updated_variants += 1
            variant_prices[str(v.pk)] = str(sell_rounded)

    # Store applied values in session so they persist in the form
    persisted_global_config = {
        k: {
            'mode': v['mode'],
            'value': str(v['value']),
            'cap': str(v['cap']) if v.get('cap') is not None else '',
        }
        for k, v in unit_configs.items()
    }

    request.session['global_pricing'] = persisted_global_config

    unit_names = {'weight': 'Weight', 'volume': 'Volume', 'pieces': 'Pieces'}
    applied = ', '.join(f'{unit_names[k]}: {v["value"]}' for k, v in unit_configs.items())
    messages.success(
        request,
        f'Global pricing applied ({applied}) — {updated_products} products, {updated_variants} variants updated.'
    )

    if is_ajax:
        return JsonResponse({
            'success': True,
            'ok': True,
            'message': f'Global pricing applied ({applied}) — {updated_products} products, {updated_variants} variants updated.',
            'updated_products': updated_products,
            'updated_variants': updated_variants,
            'variant_prices': variant_prices,
        })

    return redirect('distributors:product_catalogue')


@distributor_required
def catalogue_export(request):
    """Download current product catalogue as Excel for offline editing."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    from django.http import HttpResponse

    dist = request.user.distributor_profile
    dp_map = {dp.product_id: dp for dp in DistributorProduct.objects.filter(distributor=dist)}
    dvp_map = {dvp.variant_id: dvp for dvp in DistributorVariantPrice.objects.filter(distributor=dist)}

    products = list(_distributor_visible_products(dist)
                    .select_related('supplier', 'category')
                    .prefetch_related('variants')
                    .order_by('name'))

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Product Catalogue'

    # Header
    headers = [
        'product_id', 'variant_id', 'Product Name', 'Supplier',
        'Category', 'Variant', 'Supplier Price (₹)', 'MRP (₹)',
        'Admin Price (₹)', 'Your Price (₹)', 'Margin Mode',
        'Margin Value', 'Listed (yes/no)'
    ]
    ws.append(headers)
    hdr_fill = PatternFill('solid', fgColor='2E7D32')
    hdr_font = Font(bold=True, color='FFFFFF')
    for cell in ws[1]:
        cell.fill = hdr_fill
        cell.font = hdr_font
        cell.alignment = Alignment(horizontal='center')

    # Lock columns A & B (IDs) — grey them out as hint
    id_fill = PatternFill('solid', fgColor='EEEEEE')

    for p in products:
        dp = dp_map.get(p.pk)
        is_listed = 'yes' if (dp and dp.is_listed) else 'no'
        margin_mode = dp.margin_mode if dp else 'percent'
        margin_val = float(dp.margin_percent) if (dp and dp.margin_percent is not None) else ''

        for v in p.variants.filter(is_active=True):
            dvp = dvp_map.get(v.pk)
            base_price = float(dvp.base_price) if dvp else float(v.selling_price)
            your_price  = float(dvp.selling_price) if dvp else float(v.selling_price)
            row = [
                p.pk, v.pk, p.name, p.supplier.company_name,
                p.category.name if p.category else '',
                v.label,
                float(v.selling_price),
                float(v.mrp) if v.mrp else '',
                base_price, your_price,
                margin_mode, margin_val, is_listed,
            ]
            ws.append(row)
            # Grey out ID cells
            for col in (1, 2):
                ws.cell(ws.max_row, col).fill = id_fill

    # Column widths
    for col, width in zip('ABCDEFGHIJKLM', [10,10,28,20,16,10,14,10,14,14,14,12,12]):
        ws.column_dimensions[col].width = width

    # Add a Notes sheet explaining the columns
    notes = wb.create_sheet('Instructions')
    notes.append(['Column', 'Notes'])
    for r in [
        ('product_id / variant_id', 'DO NOT edit — used to match rows on upload'),
        ('Your Price (₹)', 'Set the price your customers pay'),
        ('Margin Mode', 'percent  OR  per_unit'),
        ('Margin Value', 'If percent: e.g. 10 means 10%. If per_unit: ₹ per kg rate (e.g. 10)'),
        ('Listed (yes/no)', 'yes = show to customers, no = hide'),
    ]:
        notes.append(r)
    notes.column_dimensions['A'].width = 28
    notes.column_dimensions['B'].width = 60

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename="catalogue.xlsx"'
    wb.save(response)
    return response


@distributor_required
def catalogue_import(request):
    """Upload edited Excel catalogue and apply changes."""
    import openpyxl
    from decimal import Decimal, InvalidOperation

    dist = request.user.distributor_profile
    visible_product_ids = set(_distributor_visible_products(dist).values_list('id', flat=True))

    if request.method != 'POST':
        return redirect('distributors:product_catalogue')

    f = request.FILES.get('catalogue_file')
    if not f:
        messages.error(request, 'No file uploaded.')
        return redirect('distributors:product_catalogue')

    try:
        wb = openpyxl.load_workbook(f, data_only=True)
        ws = wb.active
    except Exception as e:
        messages.error(request, f'Could not read file: {e}')
        return redirect('distributors:product_catalogue')

    updated = skipped = errors = 0
    from stockist.models import get_stockist_price, stockist_for, Stockist as StockistModel
    active_stockist = stockist_for(dist)

    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not row[0]:
            continue
        try:
            product_id  = int(row[0])
            variant_id  = int(row[1])
            your_price  = row[9]   # col J
            margin_mode = str(row[10] or 'percent').strip().lower()
            margin_val  = row[11]  # col L
            listed_raw  = str(row[12] or 'no').strip().lower()
        except (TypeError, ValueError, IndexError):
            errors += 1
            continue

        is_listed = listed_raw in ('yes', 'true', '1')
        if margin_mode not in ('percent', 'per_unit'):
            margin_mode = 'percent'

        if product_id not in visible_product_ids:
            skipped += 1
            continue
        try:
            product = Product.objects.get(pk=product_id)
            variant = ProductVariant.objects.get(pk=variant_id, product=product, is_active=True)
        except (Product.DoesNotExist, ProductVariant.DoesNotExist):
            skipped += 1
            continue

        # Update DistributorProduct
        dp, _ = DistributorProduct.objects.get_or_create(distributor=dist, product=product)
        dp.is_listed = is_listed
        dp.margin_mode = margin_mode
        try:
            dp.margin_percent = Decimal(str(margin_val)) if margin_val not in (None, '') else None
        except InvalidOperation:
            dp.margin_percent = None
        dp.margin_is_manual = margin_val not in (None, '')
        dp.save()

        # Update DistributorVariantPrice
        if your_price not in (None, ''):
            try:
                sell = Decimal(str(your_price))
                stockist_price, _ = get_stockist_price(product, variant, active_stockist, enterprise=dist)
                current_floor = stockist_price or variant.selling_price
                base_price = current_floor
                dvp_qs = DistributorVariantPrice.objects.filter(distributor=dist, variant=variant)
                if dvp_qs.exists():
                    base_price = max(base_price, dvp_qs.first().base_price)
                # Enforce MRP cap
                if product.show_mrp and variant.mrp:
                    sell = min(sell, variant.mrp)
                sell = max(sell, base_price)
                DistributorVariantPrice.objects.update_or_create(
                    distributor=dist, variant=variant,
                    defaults={'base_price': base_price, 'selling_price': sell, 'is_active': is_listed}
                )
                updated += 1
            except (InvalidOperation, Exception):
                errors += 1
        else:
            # Just sync is_active
            DistributorVariantPrice.objects.filter(
                distributor=dist, variant=variant
            ).update(is_active=is_listed)
            updated += 1

    messages.success(request, f'Import done — {updated} updated, {skipped} skipped, {errors} errors.')
    return redirect('distributors:product_catalogue')


# ── orders ────────────────────────────────────────────────────────────────────

@distributor_required
def order_list(request):
    dist = request.user.distributor_profile
    status_filter = request.GET.get('status', '')
    orders = Order.objects.filter(distributor=dist).select_related('customer').prefetch_related('items').order_by('-created_at')
    if status_filter:
        orders = orders.filter(status=status_filter)

    # Annotate each order with distributor margin
    for order in orders:
        order.dist_margin = sum(item.distributor_margin() for item in order.items.all())

    return render(request, 'distributors/order_list.html', {
        'dist': dist,
        'orders': orders,
        'status_filter': status_filter,
        'status_choices': Order.STATUS_CHOICES,
    })


@distributor_required
def order_detail(request, pk):
    dist = request.user.distributor_profile
    order = get_object_or_404(
        Order.objects.prefetch_related(Prefetch(
            'items',
            queryset=OrderItem.objects.select_related('product', 'variant', 'supplier_shipment'),
        )),
        pk=pk, distributor=dist,
    )
    if request.method == 'POST':
        # The supplier asked to resume a line it had flagged unavailable. Only
        # the shop knows whether the customer still wants it, so the shop decides.
        ship_action = request.POST.get('ship_action')
        if ship_action in ('resume_supply', 'decline_supply'):
            item_id = request.POST.get('item_id', '')
            item = next((i for i in order.items.all() if str(i.pk) == item_id), None)
            ship = getattr(item, 'supplier_shipment', None) if item else None
            if (ship is None or ship.status != 'unavailable' or not ship.resume_requested_at
                    or order.status not in ('pending', 'confirmed', 'stockist_picked')):
                messages.info(request, 'Nothing to confirm on that line.')
                return redirect('distributors:order_detail', pk=pk)
            try:
                from lakrish_mart.activity_logger import act as _act
            except Exception:
                _act = None
            if ship_action == 'resume_supply':
                ship.status = 'awaiting'
                ship.clear_issue()
                ship.save(update_fields=['status', 'issue_reason', 'issue_note', 'expected_ready_on',
                                         'issue_at', 'resume_requested_at', 'updated_at'])
                if _act:
                    _act.order('shop_resumed_supply', order=order, by=request.user,
                               detail=f'item#{item.pk} {item.product.name}')
                messages.success(request, f'Resumed. "{item.product.name}" is back with the supplier.')
            else:
                ship.resume_requested_at = None
                ship.save(update_fields=['resume_requested_at', 'updated_at'])
                if _act:
                    _act.order('shop_declined_resume', order=order, by=request.user,
                               detail=f'item#{item.pk} {item.product.name}')
                messages.info(
                    request,
                    f'"{item.product.name}" stays unavailable. You can still cancel the order below.')
            return redirect('distributors:order_detail', pk=pk)

        new_status = request.POST.get('status')
        # Distributor can confirm, set out_for_delivery, delivered, or cancel
        allowed = ['confirmed', 'out_for_delivery', 'delivered', 'cancelled']
        if new_status in allowed:
            order.status = new_status
            order.save()
            # Activity log: shop changed order status
            try:
                from lakrish_mart.activity_logger import act as _act
                _act.order(f'shop_set_{new_status}', order=order, by=request.user)
            except Exception:
                pass
            if new_status == 'delivered':
                try:
                    order.payment.status = 'paid'
                    order.payment.save()
                except Exception:
                    pass
                # Award reward points
                try:
                    from distributors.rewards import earn_points
                    pts = earn_points(order)
                    if pts:
                        messages.success(request, f'Order #{order.id} delivered. Customer earned {pts} reward points.')
                except Exception:
                    pass
            elif new_status == 'cancelled':
                try:
                    from distributors.rewards import reverse_order_rewards
                    reverse_order_rewards(order)
                except Exception:
                    pass
                try:
                    order.payment.status = 'refunded' if order.payment.status == 'paid' else 'failed'
                    order.payment.save()
                except Exception:
                    pass
            sync_order_ledger(order)
            sync_order_settlements(order)
            messages.success(request, f'Order #{order.id} updated to {new_status}.')
        return redirect('distributors:order_detail', pk=pk)

    dist_margin = sum(item.distributor_margin() for item in order.items.all())

    # Reward info
    reward_enabled = False
    existing_reward = 0
    customer_reward_balance = 0
    try:
        config = dist.reward_config
        reward_enabled = config.is_enabled
    except Exception:
        pass

    if reward_enabled:
        existing_txn = order.reward_transactions.filter(
            reward__distributor=dist, transaction_type='allocated'
        ).first()
        existing_reward = existing_txn.points if existing_txn else 0
        from distributors.rewards import get_balance
        bal = get_balance(order.customer, dist)
        customer_reward_balance = bal.balance

    backordered_items = order.items.filter(fulfillment_status='backordered') if order.is_partial_shipment else []

    # Where each line is with the supplier (packed / delayed / unavailable ...).
    # The shop owns the customer relationship, so it sees every line - and any
    # delay or "cannot supply" is raised as a banner so it can decide what to do.
    show_supplier_stage = (
        order.fulfillment_source == 'supplier'
        and order.status in ('pending', 'confirmed', 'stockist_picked')
    )
    supplier_issues = []
    if show_supplier_stage:
        for it in order.items.all():
            s = getattr(it, 'supplier_shipment', None)
            if s and s.status in ('delayed', 'unavailable'):
                supplier_issues.append((it, s))

    return render(request, 'distributors/order_detail.html', {
        'show_supplier_stage': show_supplier_stage,
        'supplier_issues': supplier_issues,
        'dist': dist, 'order': order,
        'dist_margin': dist_margin,
        'reward_enabled': reward_enabled,
        'existing_reward': existing_reward,
        'customer_reward_balance': customer_reward_balance,
        'backordered_items': backordered_items,
    })


# ── customers ─────────────────────────────────────────────────────────────────

@distributor_required
def customer_list(request):
    dist = request.user.distributor_profile
    customers = dist.customers.filter(is_deleted=False).order_by('username')

    # Attach zone info to each customer
    from distributors.models import CustomerZone, DeliveryZone
    cz_map = {cz.customer_id: cz.zone for cz in CustomerZone.objects.filter(
        distributor=dist
    ).select_related('zone')}
    zones = DeliveryZone.objects.filter(distributor=dist, is_active=True)

    customers_data = []
    for c in customers:
        customers_data.append({
            'user': c,
            'zone': cz_map.get(c.pk),
        })

    return render(request, 'distributors/customer_list.html', {
        'dist': dist,
        'customers': customers_data,
        'zones': zones,
    })


@distributor_required
def customer_detail(request, customer_pk):
    """Distributor views a single customer's profile and order history."""
    from accounts.models import User
    dist = request.user.distributor_profile
    customer = get_object_or_404(User, pk=customer_pk, distributor=dist, is_deleted=False)
    orders = Order.objects.filter(customer=customer, distributor=dist).order_by('-created_at')
    total_spent = sum(o.total_amount for o in orders if o.status == 'delivered')
    return render(request, 'distributors/customer_detail.html', {
        'customer': customer,
        'orders': orders,
        'total_spent': total_spent,
    })


@distributor_required
def delete_customer(request, customer_pk):
    """Soft-delete a customer removed by distributor — moves to admin's Deleted Accounts."""
    from accounts.models import User
    dist = request.user.distributor_profile
    customer = get_object_or_404(User, pk=customer_pk, distributor=dist, is_deleted=False)
    if request.method == 'POST':
        customer.soft_delete(deleted_by=f'distributor:{dist.company_name}')
        from lakrish_mart.activity_logger import act
        act.user('soft_deleted', user=customer, by=request.user,
                 detail=f'Customer removed by marketplace {dist.company_name}')
        messages.success(request, f'{customer.get_full_name() or customer.username} removed from your store.')
    return redirect('distributors:customer_list')


@distributor_required
def storefront(request):
    """Public-facing storefront preview for this distributor."""
    dist = request.user.distributor_profile
    from distributors.models import DistributorProduct, DistributorVariantPrice
    listed = DistributorProduct.objects.filter(
        distributor=dist, is_listed=True
    ).select_related('product__category', 'product__supplier').prefetch_related('product__variants')
    return render(request, 'distributors/storefront.html', {
        'dist': dist,
        'listed': listed,
    })


# ── earnings ──────────────────────────────────────────────────────────────────

@distributor_required
def earnings(request):
    dist = request.user.distributor_profile
    delivered_items = OrderItem.objects.filter(
        order__distributor=dist, order__status='delivered'
    ).select_related('product', 'variant', 'order')

    total_revenue = sum(i.subtotal() for i in delivered_items)
    total_admin_cost = sum(i.base_subtotal() for i in delivered_items)
    total_margin = total_revenue - total_admin_cost

    delivery_charges = Order.objects.filter(
        distributor=dist, status='delivered'
    ).aggregate(t=Sum('delivery_charge'))['t'] or 0

    return render(request, 'distributors/earnings.html', {
        'dist': dist,
        'delivered_items': delivered_items,
        'total_revenue': total_revenue,
        'total_admin_cost': total_admin_cost,
        'total_margin': total_margin,
        'delivery_charges': delivery_charges,
        'total_earnings': total_margin + delivery_charges,
    })


# ── settings ──────────────────────────────────────────────────────────────────

@distributor_required
def settings_view(request):
    dist = request.user.distributor_profile
    if request.method == 'POST':
        dist.delivery_charge = request.POST.get('delivery_charge', 0)
        dist.free_delivery_above = request.POST.get('free_delivery_above', 0)
        dist.default_margin_percent = request.POST.get('default_margin_percent', 0)
        dist.area = request.POST.get('area', '')
        dist.address = request.POST.get('address', '')
        dist.allow_partial_shipment = request.POST.get('allow_partial_shipment') == '1'
        dist.auto_list_new_products = request.POST.get('auto_list_new_products') == '1'
        dist.auto_list_margin_mode = request.POST.get('auto_list_margin_mode', 'percent')
        dist.auto_list_margin_value = request.POST.get('auto_list_margin_value', 0) or 0
        cap_raw = (request.POST.get('auto_list_margin_cap_percent', '') or '').strip()
        if dist.auto_list_margin_mode == 'per_unit' and cap_raw != '':
            dist.auto_list_margin_cap_percent = cap_raw
        else:
            dist.auto_list_margin_cap_percent = None
        dist.theme_color = request.POST.get('theme_color', DEFAULT_THEME_COLOR).strip() or DEFAULT_THEME_COLOR
        dist.save()
        messages.success(request, 'Settings saved.')
        return redirect('distributors:settings')
    return render(request, 'distributors/settings.html', {'dist': dist})


@distributor_required
def price_history(request):
    """Distributor sees price trends for products they have listed."""
    from products.models import VariantPriceHistory
    dist = request.user.distributor_profile
    product_id = request.GET.get('product')

    listed_product_ids = dist.dist_products.filter(is_listed=True).values_list('product_id', flat=True)
    products = Product.objects.filter(pk__in=listed_product_ids)

    history = VariantPriceHistory.objects.filter(
        variant__product__in=listed_product_ids
    ).select_related('variant__product').order_by('-changed_at')

    if product_id:
        history = history.filter(variant__product_id=product_id)

    return render(request, 'distributors/price_history.html', {
        'dist': dist,
        'history': history[:200],
        'products': products,
        'selected_product': product_id,
    })


# ── Password Reset Requests ───────────────────────────────────────────────────

@distributor_required
def reset_requests(request):
    from accounts.models import PasswordResetRequest
    from django.utils import timezone
    dist = request.user.distributor_profile
    requests_qs = PasswordResetRequest.objects.filter(
        user__distributor=dist
    ).select_related('user', 'resolved_by')

    if request.method == 'POST':
        req_id = request.POST.get('req_id')
        new_password = request.POST.get('new_password', '').strip()
        if req_id and new_password and len(new_password) >= 6:
            from accounts.models import PasswordResetRequest
            reset_req = get_object_or_404(PasswordResetRequest, pk=req_id, user__distributor=dist)
            reset_req.user.set_password(new_password)
            reset_req.user.save()
            reset_req.status = 'resolved'
            reset_req.resolved_by = request.user
            reset_req.resolved_at = timezone.now()
            reset_req.save()
            messages.success(request, f'Password reset for {reset_req.user.get_full_name() or reset_req.user.username}.')
        else:
            messages.error(request, 'Password must be at least 6 characters.')
        return redirect('distributors:reset_requests')

    return render(request, 'distributors/reset_requests.html', {'reset_requests': requests_qs})


@distributor_required
def reset_customer_password(request, customer_pk):
    """Distributor directly resets a linked customer's password."""
    from accounts.models import User
    dist = request.user.distributor_profile
    customer = get_object_or_404(User, pk=customer_pk, distributor=dist, is_deleted=False)

    if request.method == 'POST':
        new_password = request.POST.get('new_password', '').strip()
        if len(new_password) >= 6:
            customer.set_password(new_password)
            customer.save()
            messages.success(request, f'Password updated for {customer.get_full_name() or customer.username}.')
        else:
            messages.error(request, 'Password must be at least 6 characters.')
        return redirect('distributors:customer_list')

    return render(request, 'distributors/reset_customer_password.html', {'customer': customer})


@distributor_required
def delivery_slots(request):
    from .models import DeliverySlot
    dist = request.user.distributor_profile
    slots = DeliverySlot.objects.filter(distributor=dist)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add':
            DeliverySlot.objects.get_or_create(
                distributor=dist,
                day_of_week=request.POST.get('day_of_week'),
                start_time=request.POST.get('start_time'),
                defaults={'end_time': request.POST.get('end_time'), 'is_active': True}
            )
            messages.success(request, 'Slot added.')
        elif action == 'delete':
            DeliverySlot.objects.filter(pk=request.POST.get('slot_id'), distributor=dist).delete()
            messages.success(request, 'Slot removed.')
        return redirect('distributors:delivery_slots')

    return render(request, 'distributors/delivery_slots.html', {
        'slots': slots,
        'day_choices': DeliverySlot.DAY_CHOICES,
    })


@distributor_required
def earnings_summary(request):
    """Distributor earnings summary."""
    from orders.models import Order, OrderItem
    from django.db.models import Sum
    dist = request.user.distributor_profile
    orders = Order.objects.filter(distributor=dist, status='delivered')
    total_delivery = orders.aggregate(t=Sum('delivery_charge'))['t'] or 0
    items = OrderItem.objects.filter(order__distributor=dist, order__status='delivered')
    product_margin = sum(i.distributor_margin() for i in items)
    total_earned = product_margin + total_delivery
    recent = orders.order_by('-created_at')[:20]
    return render(request, 'distributors/earnings_summary.html', {
        'total_earned': total_earned,
        'product_margin': product_margin,
        'total_delivery': total_delivery,
        'recent_orders': recent,
    })


@distributor_required
def claim_customer(request):
    """Distributor searches for unassigned customers by mobile or email and links them."""
    dist = request.user.distributor_profile
    found_user = None
    already_linked = False
    search_query = ''

    if request.method == 'POST':
        action = request.POST.get('action')
        search_query = request.POST.get('identifier', '').strip()

        if action == 'search' and search_query:
            from accounts.models import User
            customer_filter = Q(role='customer') | Q(roles__icontains='customer')
            found_user = User.objects.filter(
                customer_filter,
                phone=search_query,
                distributor__isnull=True,
                is_deleted=False,
            ).first()
            if not found_user:
                found_user = User.objects.filter(
                    customer_filter,
                    email__iexact=search_query,
                    distributor__isnull=True,
                    is_deleted=False,
                ).first()
            if found_user:
                already_linked = found_user.distributor is not None

        elif action == 'claim':
            from accounts.models import User
            user_id = request.POST.get('user_id')
            try:
                customer = User.objects.filter(
                    Q(role='customer') | Q(roles__icontains='customer'),
                    pk=user_id,
                    distributor__isnull=True,
                    is_deleted=False,
                ).first()
                if customer:
                    customer.distributor = dist
                    customer.save(update_fields=['distributor'])
                    messages.success(request, f'{customer.get_full_name() or customer.username} has been linked to your store.')
                else:
                    messages.warning(request, 'Customer is unavailable for claiming.')
            except Exception:
                messages.error(request, 'Customer not found.')
            return redirect('distributors:claim_customer')

    return render(request, 'distributors/claim_customer.html', {
        'found_user': found_user,
        'already_linked': already_linked,
        'search_query': search_query,
    })


@distributor_required
def my_stock(request):
    """Distributor manages their own inventory."""
    from .models import DistributorStock, DistributorVariantPrice
    from products.models import ProductVariant, Category
    from decimal import Decimal
    dist = request.user.distributor_profile

    # Variants from all listed products (regardless of DVP is_active)
    listed_product_ids = set(DistributorProduct.objects.filter(
        distributor=dist, is_listed=True
    ).values_list('product_id', flat=True))

    listed_variant_ids = set(ProductVariant.objects.filter(
        product_id__in=listed_product_ids, is_active=True
    ).values_list('pk', flat=True))

    # Marketplace OWN products (single-role: never a supplier role). The shop
    # is their source, so their stock is the variant's own stock and the shop
    # may set it freely — up or down.
    own_variant_ids = set(
        ProductVariant.objects.filter(
            product__owner_enterprise=dist, is_active=True, product__is_deleted=False
        ).values_list('pk', flat=True)
    )
    listed_variant_ids = listed_variant_ids | own_variant_ids

    allowed_product_ids = set(
        ProductVariant.objects.filter(pk__in=listed_variant_ids).values_list('product_id', flat=True)
    )

    category_filter = request.GET.get('category', '')
    supplier_filter = request.GET.get('supplier', '')
    sort = request.GET.get('sort', 'category')
    in_stock_only = request.GET.get('in_stock_only', '') == '1'

    SORT_MAP = {
        'category': 'product__category__name',
        'supplier': 'product__supplier__company_name',
        'product': 'product__name',
        'stock': None,
    }
    order_by = SORT_MAP.get(sort, 'product__category__name')

    variants = ProductVariant.objects.filter(
        pk__in=listed_variant_ids, is_active=True
    ).select_related('product__supplier', 'product__category')

    if category_filter:
        variants = variants.filter(product__category_id=category_filter)
    if supplier_filter:
        variants = variants.filter(product__supplier_id=supplier_filter)

    if order_by:
        variants = variants.order_by(order_by, 'product__name', 'label')
    else:
        variants = variants.order_by('product__name', 'label')

    stock_map = {s.variant_id: s for s in DistributorStock.objects.filter(distributor=dist)}
    dvp_map = {dvp.variant_id: dvp for dvp in DistributorVariantPrice.objects.filter(distributor=dist)}

    if request.method == 'POST':
        action = request.POST.get('action', '')

        if action == 'auto_restock_settings':
            # Stage 7g: opt-in auto-restock (threshold + reorder quantity).
            dist.auto_restock_enabled = request.POST.get('enabled') == '1'
            try:
                dist.auto_restock_threshold = max(0, int(request.POST.get('threshold', 5)))
                dist.auto_restock_quantity = max(1, int(request.POST.get('quantity', 10)))
            except ValueError:
                pass
            try:
                from decimal import Decimal as _D, InvalidOperation as _IO
                raw_cap = (request.POST.get('max_price') or '0').strip()
                dist.auto_restock_max_price = max(_D('0'), _D(raw_cap or '0'))
            except (_IO, ValueError):
                pass
            dist.save(update_fields=['auto_restock_enabled', 'auto_restock_threshold',
                                     'auto_restock_quantity', 'auto_restock_max_price'])
            if dist.auto_restock_enabled:
                from stockist.auto_restock import check_marketplace
                made = check_marketplace(dist)
                messages.success(
                    request,
                    f'Auto-restock ON — at ≤{dist.auto_restock_threshold} units a restock '
                    f'request for {dist.auto_restock_quantity} units is filed automatically.'
                    + (f' {made} request(s) filed right now.' if made else '')
                )
            else:
                messages.success(request, 'Auto-restock turned off.')
            from django.urls import reverse as _rev
            return redirect(_rev('distributors:my_stock'))

        if action == 'toggle_auto':
            # Per-item auto-restock switch (chain products only).
            from .models import DistributorStock as _DS2
            vid = request.POST.get('variant_id')
            variant = ProductVariant.objects.filter(pk=vid).first()
            if variant and int(vid) not in own_variant_ids:
                row, _ = _DS2.objects.get_or_create(
                    distributor=dist, variant=variant, defaults={'quantity': 0}
                )
                row.auto_restock_exempt = not row.auto_restock_exempt
                row.save(update_fields=['auto_restock_exempt'])
                state = 'excluded from' if row.auto_restock_exempt else 'included in'
                messages.success(request, f'{variant.product.name} {variant.label} is now {state} auto-restock.')
            from django.urls import reverse as _rev
            return redirect(_rev('distributors:my_stock'))

        # ── Stage 7 stock rules ──────────────────────────────────────────────
        # Shared-chain stock: manual change can only REDUCE (offline sale, own
        # use, wastage). Increases arrive only through delivered restocks.
        if action == 'reduce':
            from django.db import transaction as _tx
            from distributors.models import consume_stock_fifo, DistributorStock as _DS
            vid = request.POST.get('variant_id')
            try:
                units = int(request.POST.get('units', '0'))
            except ValueError:
                units = 0
            if units <= 0:
                messages.error(request, 'Enter how many units to reduce.')
            else:
                try:
                    with _tx.atomic():
                        consume_stock_fifo(dist, ProductVariant.objects.get(pk=vid), units)
                        stock_obj = _DS.objects.get(distributor=dist, variant_id=vid)
                        stock_obj.refresh_quantity()
                        stock_obj.save()
                    messages.success(request, f'Stock reduced by {units} unit(s).')
                except (ProductVariant.DoesNotExist, ValueError) as e:
                    messages.error(
                        request,
                        f'{e} — stock can only be reduced manually; increases '
                        'come from delivered restock requests.'
                    )

        elif action == 'reduce_loose':
            from distributors.models import DistributorLooseStock as _DLS
            pid = request.POST.get('product_id')
            try:
                grams = int(float(request.POST.get('kg', '0')) * 1000)
            except ValueError:
                grams = 0
            ls = _DLS.objects.filter(distributor=dist, product_id=pid).first()
            if not ls or grams <= 0:
                messages.error(request, 'Enter how many kg to reduce.')
            elif grams > ls.quantity_grams:
                messages.error(request, f'Only {ls.quantity_grams}g loose available.')
            else:
                ls.quantity_grams -= grams
                ls.save(update_fields=['quantity_grams'])
                messages.success(request, f'Loose stock reduced by {grams}g.')

        # Own products: the shop IS the source (its own local supplier), so it
        # may set stock in BOTH directions.
        elif action == 'own_stock':
            vid = request.POST.get('variant_id')
            try:
                qty = int(request.POST.get('stock', '-1'))
            except ValueError:
                qty = -1
            if vid and int(vid) in own_variant_ids and qty >= 0:
                ProductVariant.objects.filter(pk=vid).update(stock=qty)
                messages.success(request, f'Own-product stock set to {qty}.')
            else:
                messages.error(request, 'Could not update own-product stock.')

        if action in ('reduce', 'reduce_loose', 'own_stock'):
            if action in ('reduce', 'reduce_loose'):
                from stockist.auto_restock import check_marketplace
                made = check_marketplace(dist)
                if made:
                    messages.info(request, f'Auto-restock: {made} request(s) filed.')
            from django.urls import reverse as _rev
            return redirect(_rev('distributors:my_stock'))

        if action == 'restock':
            from distributors.models import RestockRequest, DistributorLooseStock
            from decimal import Decimal, InvalidOperation
            restock_type = request.POST.get('restock_type', 'packed')  # packed or loose

            if restock_type == 'loose':
                # Stage 7: loose stock is NEVER added directly any more — it
                # must arrive through a delivered restock request, same as
                # packed stock. (Increase only by order; reduce any time.)
                product_id = request.POST.get('product_id')
                loose_kg = request.POST.get('loose_kg', '').strip()
                if product_id and loose_kg:
                    try:
                        from products.models import Product as Prod
                        product_obj = Prod.objects.get(pk=product_id, pk__in=allowed_product_ids)
                        if product_obj.owner_enterprise_id:
                            raise ValueError('Own products: set their stock directly instead.')
                        grams = int(float(loose_kg) * 1000)
                        rep_variant = ProductVariant.objects.filter(
                            product=product_obj, is_active=True, unit_grams__gt=0
                        ).order_by('unit_grams').first()
                        if grams <= 0 or rep_variant is None:
                            raise ValueError('This product has no weighed pack sizes.')
                        RestockRequest.objects.create(
                            distributor=dist,
                            variant=rep_variant,
                            quantity_requested=1,
                            loose_grams=grams,
                            note=f'LOOSE {loose_kg}kg — from My Stock page',
                        )
                        messages.success(
                            request,
                            f'Loose request for {loose_kg}kg of {product_obj.name} sent. '
                            'Stock is added when it is delivered.'
                        )
                    except Exception as e:
                        messages.error(request, f'Error: {e}')
            else:
                # Packed: one RestockRequest per variant with qty > 0
                # fulfillment_method is decided by stockist, not distributor
                submitted = False
                for key, val in request.POST.items():
                    if key.startswith('qty_') and val.strip() and int(val.strip()) > 0:
                        vid = key[4:]
                        try:
                            if int(vid) in own_variant_ids:
                                continue  # own products: the shop IS the source
                            variant_obj = ProductVariant.objects.get(pk=vid, pk__in=listed_variant_ids)
                            RestockRequest.objects.create(
                                distributor=dist,
                                variant=variant_obj,
                                quantity_requested=int(val.strip()),
                                note='Requested from My Stock page',
                            )
                            submitted = True
                        except Exception:
                            pass
                if submitted:
                    messages.success(request, 'Restock request(s) sent.')
                else:
                    messages.warning(request, 'Enter quantity for at least one size.')

        from django.urls import reverse
        base = reverse('distributors:my_stock')
        qs = f'?category={category_filter}&supplier={supplier_filter}&sort={sort}'
        if in_stock_only:
            qs += '&in_stock_only=1'
        return redirect(base + qs)

    stock_data = []
    for v in variants:
        stock_obj = stock_map.get(v.pk)
        dvp = dvp_map.get(v.pk)
        is_own = v.pk in own_variant_ids
        if is_own:
            # The shop is the source: its stock IS the variant's stock.
            qty = v.stock
            selling_price = v.selling_price
            source_label = '🏭 Own'
        else:
            qty = stock_obj.quantity if stock_obj else 0
            selling_price = dvp.selling_price if dvp else v.selling_price
            source_label = ''
        stock_value = Decimal(qty) * selling_price

        # Batch breakdown
        batches = []
        if stock_obj:
            from distributors.models import DistributorStockBatch
            batches = list(stock_obj.batches.filter(quantity__gt=0).order_by('received_at'))

        stock_data.append({
            'variant': v,
            'quantity': qty,
            'selling_price': selling_price,
            'stock_value': stock_value,
            'updated_at': stock_obj.updated_at if stock_obj else None,
            'low_stock': 0 < qty <= 5,
            'source_label': source_label,
            'batches': batches,
            'multi_batch': len(batches) > 1,
            'is_own': is_own,
            'auto_exempt': stock_obj.auto_restock_exempt if stock_obj else False,
        })

    if sort == 'stock':
        stock_data.sort(key=lambda x: x['quantity'])

    if in_stock_only:
        stock_data = [s for s in stock_data if s['quantity'] > 0]

    total_value = sum(s['stock_value'] for s in stock_data)
    in_stock = sum(1 for s in stock_data if s['quantity'] > 0)
    low_stock_count = sum(1 for s in stock_data if s['low_stock'])
    out_of_stock = sum(1 for s in stock_data if s['quantity'] == 0)

    from suppliers.models import Supplier
    categories = Category.objects.filter(
        products__variants__pk__in=listed_variant_ids
    ).distinct().order_by('name')
    suppliers = Supplier.objects.filter(
        products__variants__pk__in=listed_variant_ids
    ).distinct().order_by('company_name')

    # Build product_variants_map for restock modal
    from collections import defaultdict
    from distributors.models import DistributorLooseStock

    loose_map = {
        ls.product_id: ls
        for ls in DistributorLooseStock.objects.filter(distributor=dist)
    }

    # Annotate stock_data with loose availability
    for s in stock_data:
        loose = loose_map.get(s['variant'].product_id)
        s['loose_grams'] = loose.quantity_grams if loose else 0
        s['loose_units'] = loose.available_units(s['variant']) if loose else 0

    # Group stock_data by product AFTER loose annotation
    from collections import OrderedDict as OD
    grouped_stock = OD()
    for s in stock_data:
        pid = s['variant'].product_id
        if pid not in grouped_stock:
            grouped_stock[pid] = {
                'product': s['variant'].product,
                'total_qty': 0,
                'total_value': Decimal('0'),
                'total_loose_grams': s['loose_grams'],
                'variants': [],
                'has_stock': False,
                'low_stock': False,
                'source_label': s['source_label'],
            }
        g = grouped_stock[pid]
        g['total_qty'] += s['quantity']
        g['total_value'] += s['stock_value']
        g['has_stock'] = g['has_stock'] or s['quantity'] > 0
        g['low_stock'] = g['low_stock'] or s['low_stock']
        g['variants'].append(s)
    grouped_stock = list(grouped_stock.values())

    # Build product_variants_map for restock modal
    from collections import defaultdict
    from distributors.models import DistributorLooseStock
    product_variants_map = defaultdict(list)
    for s in stock_data:
        v = s['variant']
        dvp = dvp_map.get(v.pk)
        product_variants_map[v.product_id].append({
            'id': v.pk,
            'label': v.label,
            'stock': s['quantity'],
            'unit_grams': v.unit_grams,
            'base_price': float(dvp.base_price) if dvp else float(v.selling_price),
        })

    products_for_modal = []
    seen = set()
    for s in stock_data:
        v = s['variant']
        pid = v.product_id
        if s.get('is_own'):
            continue  # own products: set stock directly, no chain restock
        if pid not in seen:
            seen.add(pid)
            loose = loose_map.get(pid)
            products_for_modal.append({
                'id': pid,
                'name': v.product.name,
                'unit': v.product.master_unit,
                'supplier_id': v.product.supplier_id,
                'supplier_name': v.product.supplier.company_name,
                'allow_loose': v.product.allow_loose_distribution,
                'variants': product_variants_map[pid],
                'loose_grams': loose.quantity_grams if loose else 0,
            })

    return render(request, 'distributors/my_stock.html', {
        'dist': dist,
        'stock_data': stock_data,
        'grouped_stock': grouped_stock,
        'categories': categories,
        'suppliers': suppliers,
        'category_filter': category_filter,
        'supplier_filter': supplier_filter,
        'sort': sort,
        'in_stock_only': in_stock_only,
        'total_variants': len(stock_data),
        'in_stock': in_stock,
        'low_stock_count': low_stock_count,
        'out_of_stock': out_of_stock,
        'total_value': total_value,
        'products_for_modal': products_for_modal,
    })


@distributor_required
def restock_requests(request):
    """Distributor views all their restock requests and status."""
    from distributors.models import RestockRequest
    dist = request.user.distributor_profile
    status_filter = request.GET.get('status', '')
    requests_qs = RestockRequest.objects.filter(
        distributor=dist
    ).select_related('variant__product__supplier', 'variant__product__category').order_by('-requested_at')
    if status_filter:
        requests_qs = requests_qs.filter(status=status_filter)
    return render(request, 'distributors/restock_requests.html', {
        'requests': requests_qs,
        'status_filter': status_filter,
        'status_choices': RestockRequest.STATUS_CHOICES,
    })


# ── Reward Points ─────────────────────────────────────────────────────────────

@distributor_required
def reward_settings(request):
    """Distributor enables and configures reward points program."""
    from distributors.reward_models import RewardConfig, RewardPoints
    from django.db.models import Sum
    dist = request.user.distributor_profile
    config, _ = RewardConfig.objects.get_or_create(
        distributor=dist,
        defaults={
            'is_enabled': False,
            'min_points_to_redeem': 10,
            'max_redeem_percent': 20,
        }
    )

    if request.method == 'POST':
        config.is_enabled = request.POST.get('is_enabled') == '1'
        config.points_earn_percent = request.POST.get('points_earn_percent', 5)
        config.min_points_to_redeem = request.POST.get('min_points_to_redeem', 10)
        config.max_redeem_percent = request.POST.get('max_redeem_percent', 20)
        config.welcome_bonus_enabled = request.POST.get('welcome_bonus_enabled') == '1'
        config.welcome_bonus_points = int(request.POST.get('welcome_bonus_points', 0))
        config.save()
        messages.success(request, 'Reward settings saved.')
        return redirect('distributors:reward_settings')

    top_customers = RewardPoints.objects.filter(
        distributor=dist
    ).select_related('customer').order_by('-balance')[:20]

    total_points_issued = RewardPoints.objects.filter(
        distributor=dist
    ).aggregate(t=Sum('total_earned'))['t'] or 0

    return render(request, 'distributors/reward_settings.html', {
        'config': config,
        'top_customers': top_customers,
        'total_points_issued': total_points_issued,
    })


@distributor_required
def reward_adjust(request):
    """Distributor manually adjusts a customer's points."""
    from distributors.reward_models import RewardPoints, RewardTransaction
    dist = request.user.distributor_profile
    if request.method == 'POST':
        customer_id = request.POST.get('customer_id')
        points = int(request.POST.get('points', 0))
        note = request.POST.get('note', '').strip()
        try:
            from accounts.models import User
            customer = User.objects.get(pk=customer_id, distributor=dist)
            balance, _ = RewardPoints.objects.get_or_create(
                customer=customer, distributor=dist,
                defaults={'balance': 0, 'total_earned': 0, 'total_redeemed': 0}
            )
            balance.balance = max(0, balance.balance + points)
            if points > 0:
                balance.total_earned += points
            balance.save()
            RewardTransaction.objects.create(
                reward=balance, transaction_type='adjusted',
                points=points, note=note or f'Manual adjustment by distributor'
            )
            messages.success(request, f'Points adjusted for {customer.get_full_name() or customer.username}.')
        except Exception as e:
            messages.error(request, str(e))
    return redirect('distributors:reward_settings')


@distributor_required
def fulfill_backorder(request, order_pk):
    """Distributor marks back-ordered items as shipped when stock arrives."""
    from orders.models import OrderItem
    dist = request.user.distributor_profile
    order = get_object_or_404(Order, pk=order_pk, distributor=dist)

    if request.method == 'POST':
        item_ids = request.POST.getlist('item_ids')
        updated = 0
        for item_id in item_ids:
            updated += OrderItem.objects.filter(
                pk=item_id, order=order, fulfillment_status='backordered'
            ).update(fulfillment_status='shipped')

        # If all items are now shipped/delivered, clear the partial flag
        remaining = order.items.filter(fulfillment_status='backordered').count()
        if remaining == 0 and order.is_partial_shipment:
            order.is_partial_shipment = False
            order.save(update_fields=['is_partial_shipment'])

        messages.success(request, f'{updated} item(s) marked as shipped.')

    return redirect('distributors:order_detail', pk=order_pk)


@distributor_required
def allocate_reward(request, order_pk):
    """Distributor gives reward points to customer from their margin."""
    dist = request.user.distributor_profile
    order = get_object_or_404(Order, pk=order_pk, distributor=dist, status='delivered')

    if request.method == 'POST':
        try:
            points = int(request.POST.get('points', 0))
            note = request.POST.get('note', '').strip()
            if points <= 0:
                messages.error(request, 'Points must be greater than 0.')
            else:
                # Check not already allocated for this order
                existing = order.reward_transactions.filter(
                    reward__distributor=dist, transaction_type='allocated'
                ).exists()
                if existing:
                    messages.warning(request, 'Reward already allocated for this order.')
                else:
                    from distributors.rewards import allocate_points
                    allocate_points(dist, order.customer, points, order=order,
                                    note=note or f'Reward for order #{order.pk}')
                    messages.success(request, f'₹{points} reward points given to {order.customer.get_full_name() or order.customer.username}.')
        except (ValueError, TypeError):
            messages.error(request, 'Invalid points value.')

    return redirect('distributors:order_detail', pk=order_pk)


@distributor_required
def delivery_zones(request):
    """Manage delivery zones and assign customers to zones."""
    from distributors.models import DeliveryZone, CustomerZone
    dist = request.user.distributor_profile
    zones = DeliveryZone.objects.filter(distributor=dist)
    customers = dist.customers.filter(is_active=True, is_deleted=False).order_by('username')

    # Build customer→zone map
    cz_map = {cz.customer_id: cz.zone_id for cz in CustomerZone.objects.filter(distributor=dist)}

    # Attach zone_id to each customer for template
    customers_with_zones = []
    for c in customers:
        customers_with_zones.append({
            'user': c,
            'zone_id': cz_map.get(c.pk),
        })

    if request.method == 'POST':
        action = request.POST.get('action', '')

        if action == 'add_zone':
            name = request.POST.get('zone_name', '').strip()
            charge = request.POST.get('zone_charge', '0').strip()
            free_above = request.POST.get('zone_free_above', '0').strip()
            pincodes = request.POST.get('zone_pincodes', '').strip()
            if name:
                DeliveryZone.objects.get_or_create(
                    distributor=dist, name=name,
                    defaults={
                        'delivery_charge': charge or 0,
                        'free_delivery_above': free_above or 0,
                        'pincodes': pincodes,
                    }
                )
                messages.success(request, f'Zone "{name}" created.')
            else:
                messages.error(request, 'Zone name is required.')

        elif action == 'delete_zone':
            zone_id = request.POST.get('zone_id')
            DeliveryZone.objects.filter(pk=zone_id, distributor=dist).delete()
            messages.success(request, 'Zone deleted.')

        elif action == 'edit_zone':
            zone_id = request.POST.get('zone_id')
            zone = DeliveryZone.objects.filter(pk=zone_id, distributor=dist).first()
            if zone:
                zone.name = request.POST.get('zone_name', zone.name).strip()
                zone.delivery_charge = request.POST.get('zone_charge', zone.delivery_charge) or 0
                zone.free_delivery_above = request.POST.get('zone_free_above', 0) or 0
                zone.pincodes = request.POST.get('zone_pincodes', '').strip()
                zone.save()
                messages.success(request, f'Zone "{zone.name}" updated.')

        elif action == 'assign_customers':
            # Bulk assign customers to zones
            for customer in customers:
                zone_id = request.POST.get(f'zone_{customer.pk}', '').strip()
                if zone_id:
                    zone = DeliveryZone.objects.filter(pk=zone_id, distributor=dist).first()
                    if zone:
                        CustomerZone.objects.update_or_create(
                            customer=customer, distributor=dist,
                            defaults={'zone': zone}
                        )
                else:
                    # Remove zone assignment
                    CustomerZone.objects.filter(customer=customer, distributor=dist).delete()
            messages.success(request, 'Customer zones updated.')

        return redirect('distributors:delivery_zones')

    return render(request, 'distributors/delivery_zones.html', {
        'dist': dist,
        'zones': zones,
        'customers': customers_with_zones,
    })
