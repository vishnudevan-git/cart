from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.db.models import Sum, Count, Q
from django.utils import timezone as dj_timezone
from django.http import HttpResponse
from django.http import JsonResponse
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST
from pathlib import Path
from django.conf import settings
from accounts.decorators import role_required
from accounts.models import User
from suppliers.models import Supplier
from products.models import Product, Category, ProductVariant
from orders.models import Order
from payments.models import Payment, PaymentLedgerEntry, CommissionRule
from distributors.models import Distributor, DistributorProduct, DistributorVariantPrice, Wholesaler
from admin_panel.models import SiteSettings
from admin_panel.runbook import collect_runbook_data


admin_required = role_required('admin', set_active_role='admin', allow_superuser=True)


def project_runbook(request):
    """Live runbook page. Regenerates from current codebase data on each request."""
    data = collect_runbook_data()
    site = SiteSettings.get()
    context = {
        **data,
        'generated_at': dj_timezone.now(),
        'demo_mode_enabled': site.demo_mode_enabled,
        'demo_allow_quick_login': site.demo_allow_quick_login,
        'demo_block_transactions': site.demo_block_transactions,
    }

    html = render_to_string('project/runbook.html', context, request=request)
    # Keep a shareable root snapshot in sync whenever the live runbook is viewed.
    output_path = Path(settings.BASE_DIR) / 'project_runbook.html'
    output_path.write_text(html, encoding='utf-8')
    return HttpResponse(html)


@admin_required
def price_track_validator(request):
    """Interactive calculator to validate supplier->stockist->distributor->customer pricing."""
    return render(request, 'project/price_track_validator.html')


@admin_required
def price_trace(request):
    """Transparent price breakdown for any product × shop.

    Shows, for the chosen Marketplace and product, exactly how the price is
    built at each stage: supplier price -> + stockist charges -> + wholesale
    Distributor margin = cost floor -> shop's selling price (clamped to the
    floor and, if MRP is shown, to MRP). Read-only — purely for transparency
    and debugging the chain.
    """
    from decimal import Decimal
    from distributors.models import DistributorVariantPrice
    from stockist.models import stockist_for, get_stockist_price

    shops = Distributor.objects.filter(status='active').order_by('company_name')
    shop_id = request.GET.get('shop') or ''
    product_id = request.GET.get('product') or ''

    shop = Distributor.objects.filter(pk=shop_id).first() if shop_id else None

    # Products the chosen shop actually lists (shared catalogue + own products).
    products = []
    if shop:
        listed_ids = DistributorProduct.objects.filter(
            distributor=shop, is_listed=True
        ).values_list('product_id', flat=True)
        products = (Product.all_objects.filter(pk__in=listed_ids, is_deleted=False)
                    .select_related('supplier', 'category').order_by('name'))

    product = None
    if shop and product_id:
        product = Product.all_objects.filter(pk=product_id).first()

    rows = []
    stk = stockist_for(shop) if shop else None
    wholesaler = shop.wholesaler if shop else None

    if shop and product:
        own = product.owner_enterprise_id == shop.id
        dvp_map = {
            d.variant_id: d for d in DistributorVariantPrice.objects.filter(
                distributor=shop, variant__product=product, is_active=True)
        }
        for v in product.variants.filter(is_active=True).order_by('selling_price'):
            if own or getattr(product, 'direct_supply', False):
                # Own products / direct-supply: no chain — supplier price IS the base.
                floor = v.selling_price
                breakdown = []
            else:
                floor, breakdown = get_stockist_price(product, v, stk, enterprise=shop)
            dvp = dvp_map.get(v.pk)
            sell = dvp.selling_price if dvp else None
            rows.append({
                'variant': v,
                'supplier_price': v.selling_price,
                'breakdown': breakdown,
                'floor': floor,
                'mrp': v.mrp,
                'sell': sell,
                'below_floor': (sell is not None and sell < floor),
                'own': own,
                'direct': getattr(product, 'direct_supply', False),
            })

    return render(request, 'admin_panel/price_trace.html', {
        'shops': shops,
        'selected_shop': shop,
        'selected_shop_id': str(shop_id),
        'products': products,
        'selected_product': product,
        'selected_product_id': str(product_id),
        'stockist': stk,
        'wholesaler': wholesaler,
        'rows': rows,
    })


def product_lifecycle_api(request):
    """Realtime lifecycle data for interactive pricing visual page."""
    from decimal import Decimal
    from stockist.models import Stockist, ProcessingCharge, get_stockist_price

    def _storefront_visible_products(distributor):
        listed_ids = DistributorProduct.objects.filter(
            distributor=distributor,
            is_listed=True,
        ).values_list('product_id', flat=True)

        qs = Product.objects.filter(
            (Q(status='approved', charges_confirmed=True) |
             Q(status='approved', direct_supply=True) |
             Q(status='pending', charges_confirmed=True, pk__in=listed_ids)),
            pk__in=listed_ids,
            is_deleted=False,
            variants__is_active=True,
        ).distinct()

        if distributor.hide_out_of_stock_products:
            qs = qs.filter(variants__stock__gt=0)
        return qs.distinct()

    action = request.GET.get('action', '').strip()

    if action == 'init':
        distributors = []
        preferred_distributor_id = None
        for d in Distributor.objects.filter(status='active').order_by('company_name'):
            visible_count = _storefront_visible_products(d).count()
            distributors.append({'id': d.pk, 'name': d.company_name, 'visible_count': visible_count})
            if preferred_distributor_id is None and visible_count > 0:
                preferred_distributor_id = d.pk

        if preferred_distributor_id is None and distributors:
            preferred_distributor_id = distributors[0]['id']
        stockists = [
            {'id': s.pk, 'name': s.company_name}
            for s in Stockist.objects.filter(status='active').order_by('company_name')
        ]

        return JsonResponse({
            'ok': True,
            'distributors': distributors,
            'preferred_distributor_id': preferred_distributor_id,
            'stockists': stockists,
            'charge_type_choices': [
                {'key': k, 'label': v} for k, v in ProcessingCharge.CHARGE_TYPE_CHOICES
            ],
            'scope_choices': [
                {'key': k, 'label': v} for k, v in ProcessingCharge.SCOPE_CHOICES
            ],
        })

    if action == 'products':
        distributor_id = request.GET.get('distributor_id')
        if not distributor_id:
            return JsonResponse({'ok': False, 'error': 'distributor_id is required'}, status=400)

        distributor = get_object_or_404(Distributor, pk=distributor_id, status='active')
        products = []
        for p in _storefront_visible_products(distributor).select_related('supplier').order_by('name'):
            products.append({
                'id': p.pk,
                'name': p.name,
                'supplier': p.supplier.company_name,
            })

        return JsonResponse({
            'ok': True,
            'products': products,
            'hide_out_of_stock_products': distributor.hide_out_of_stock_products,
        })

    if action == 'variants':
        product_id = request.GET.get('product_id')
        distributor_id = request.GET.get('distributor_id')
        if not product_id or not distributor_id:
            return JsonResponse({'ok': False, 'error': 'product_id and distributor_id are required'}, status=400)

        distributor = get_object_or_404(Distributor, pk=distributor_id, status='active')
        visible_ids = _storefront_visible_products(distributor).values_list('pk', flat=True)
        if int(product_id) not in set(visible_ids):
            return JsonResponse({'ok': False, 'error': 'Product is not visible in this distributor storefront'}, status=400)

        product = get_object_or_404(Product.objects.select_related('supplier'), pk=product_id)
        variants = []
        for v in product.variants.filter(is_active=True).order_by('selling_price'):
            if distributor.hide_out_of_stock_products and (v.stock or 0) <= 0:
                continue
            variants.append({
                'id': v.pk,
                'label': v.label,
                'selling_price': float(v.selling_price),
                'mrp': float(v.mrp) if v.mrp else None,
                'unit_grams': v.unit_grams,
                'show_mrp': product.show_mrp,
                'master_unit': product.master_unit,
            })

        return JsonResponse({'ok': True, 'variants': variants})

    if action == 'price':
        product_id = request.GET.get('product_id')
        variant_id = request.GET.get('variant_id')
        distributor_id = request.GET.get('distributor_id')
        stockist_id = request.GET.get('stockist_id')

        if not all([product_id, variant_id, distributor_id, stockist_id]):
            return JsonResponse({'ok': False, 'error': 'product_id, variant_id, distributor_id, stockist_id are required'}, status=400)

        product = get_object_or_404(Product.objects.select_related('supplier'), pk=product_id)
        variant = get_object_or_404(ProductVariant, pk=variant_id, product=product, is_active=True)
        distributor = get_object_or_404(Distributor, pk=distributor_id, status='active')
        stockist = get_object_or_404(Stockist, pk=stockist_id, status='active')

        visible_ids = _storefront_visible_products(distributor).values_list('pk', flat=True)
        if int(product_id) not in set(visible_ids):
            return JsonResponse({'ok': False, 'error': 'Selected product is not visible on this distributor storefront'}, status=400)

        supplier_price = variant.selling_price
        stockist_price, breakdown = get_stockist_price(product, variant, stockist)

        dp = DistributorProduct.objects.filter(distributor=distributor, product=product).first()
        dvp = DistributorVariantPrice.objects.filter(distributor=distributor, variant=variant, is_active=True).first()

        margin_mode = 'percent'
        margin_value = Decimal('0')
        if dp and dp.margin_percent is not None:
            margin_mode = dp.margin_mode or 'percent'
            margin_value = Decimal(str(dp.margin_percent))
        else:
            margin_mode = distributor.auto_list_margin_mode or 'percent'
            margin_value = Decimal(str(distributor.auto_list_margin_value or 0))

        pack = Decimal(str(variant.unit_grams or 0))
        multiplier = Decimal('1')
        if margin_mode == 'per_unit':
            if product.master_unit in ('g', 'ml') and pack > 0:
                multiplier = pack / Decimal('1000')
            elif product.master_unit == 'qty' and pack > 0:
                multiplier = pack

        if margin_mode == 'per_unit':
            distributor_calc = stockist_price + (margin_value * multiplier)
        else:
            distributor_calc = stockist_price * (Decimal('1') + (margin_value / Decimal('100')))

        cap_value = distributor.auto_list_margin_cap_percent
        cap_applied = False
        if margin_mode == 'per_unit' and cap_value is not None and cap_value > 0:
            cap_limit = supplier_price * (Decimal('1') + (Decimal(str(cap_value)) / Decimal('100')))
            if distributor_calc > cap_limit:
                distributor_calc = cap_limit
                cap_applied = True

        mrp_applied = False
        if product.show_mrp and variant.mrp:
            if distributor_calc > variant.mrp:
                distributor_calc = variant.mrp
                mrp_applied = True

        distributor_calc = max(distributor_calc, supplier_price)
        distributor_calc = Decimal(str(int(round(float(distributor_calc)))))

        live_customer_price = dvp.selling_price if dvp else distributor_calc
        live_customer_price = max(live_customer_price, stockist_price)
        below_stockist = live_customer_price < stockist_price
        loss_amount = (stockist_price - live_customer_price) if below_stockist else Decimal('0')

        return JsonResponse({
            'ok': True,
            'product': {
                'id': product.pk,
                'name': product.name,
                'supplier': product.supplier.company_name,
                'master_unit': product.master_unit,
                'show_mrp': product.show_mrp,
                'charges_confirmed': product.charges_confirmed,
            },
            'variant': {
                'id': variant.pk,
                'label': variant.label,
                'unit_grams': variant.unit_grams,
                'supplier_price': float(supplier_price),
                'mrp': float(variant.mrp) if variant.mrp else None,
            },
            'distributor': {
                'id': distributor.pk,
                'name': distributor.company_name,
                'margin_mode': margin_mode,
                'margin_value': float(margin_value),
                'auto_list_margin_cap_percent': float(cap_value) if cap_value is not None else None,
            },
            'stockist': {
                'id': stockist.pk,
                'name': stockist.company_name,
                'price': float(stockist_price),
                'charges_breakdown': [
                    {
                        'stage': b['stage'],
                        'type': b['type'],
                        'scope': b['scope'],
                        'value': float(b['value']),
                        'amount': float(b['amount']),
                    }
                    for b in breakdown
                ],
            },
            'price': {
                'supplier': float(supplier_price),
                'stockist': float(stockist_price),
                'distributor_calculated': float(distributor_calc),
                'distributor_live': float(live_customer_price),
                'customer': float(live_customer_price),
                'cap_applied': cap_applied,
                'mrp_applied': mrp_applied,
                'used_live_dvp': bool(dvp),
                'below_stockist': below_stockist,
                'loss_amount': float(loss_amount),
            },
        })

    return JsonResponse({'ok': False, 'error': 'Unsupported action'}, status=400)


# ── Dashboard ────────────────────────────────────────────────────────────────

@admin_required
def dashboard(request):
    total_orders = Order.objects.count()
    pending_orders = Order.objects.filter(status='pending').count()
    total_revenue = Payment.objects.filter(status='paid').aggregate(t=Sum('amount'))['t'] or 0
    total_suppliers = Supplier.objects.filter(status='approved').count()
    pending_products = Product.objects.filter(status='pending').count()
    total_distributors = Distributor.objects.filter(status='active').count()
    recent_orders = Order.objects.select_related('customer', 'distributor').order_by('-created_at')[:10]
    from support.models import Ticket as SupportTicket
    pending_support_count = SupportTicket.objects.filter(status='open').count()
    return render(request, 'admin_panel/dashboard.html', {
        'total_orders': total_orders,
        'pending_orders': pending_orders,
        'total_revenue': total_revenue,
        'total_suppliers': total_suppliers,
        'pending_products': pending_products,
        'total_distributors': total_distributors,
        'recent_orders': recent_orders,
        'pending_support_count': pending_support_count,
    })


# ── Supplier Management ───────────────────────────────────────────────────────

@admin_required
def supplier_list(request):
    sort = request.GET.get('sort', 'company_name')
    status_filter = request.GET.get('status', 'approved')
    search = request.GET.get('q', '').strip()
    SORT_MAP = {
        'name': 'company_name', '-name': '-company_name',
        'category': 'category', '-category': '-category',
        'status': 'status', '-status': '-status',
    }
    order_by = SORT_MAP.get(sort, 'company_name')
    if status_filter == 'deleted':
        # Deleted tab: soft-deleted (not purged). Lazy-purge overdue ones first.
        from admin_panel.purge import purge_due_accounts
        purge_due_accounts()
        suppliers = Supplier.objects.select_related('user').filter(
            user__is_deleted=True, user__is_purged=False).order_by(order_by)
    else:
        suppliers = Supplier.objects.select_related('user').filter(user__is_deleted=False).order_by(order_by)
        if status_filter and status_filter != 'all':
            suppliers = suppliers.filter(status=status_filter)
    if search:
        suppliers = (suppliers.filter(company_name__icontains=search) |
                     suppliers.filter(user__username__icontains=search))

    active_count = Supplier.objects.filter(status='approved', user__is_deleted=False).count()
    suspended_count = Supplier.objects.filter(status='suspended', user__is_deleted=False).count()
    deleted_count = Supplier.objects.filter(user__is_deleted=True, user__is_purged=False).count()

    from django.urls import reverse
    return render(request, 'admin_panel/supplier_list.html', {
        'suppliers': suppliers,
        'sort': sort,
        'status_filter': status_filter,
        'active_count': active_count,
        'suspended_count': suspended_count,
        'deleted_count': deleted_count,
        'search': search,
        # Common partner-list layout config:
        'page_title': 'Suppliers',
        'active_nav': 'supplier_list',
        'show_tabs': True,
        'tab_active_value': 'approved',
        'show_search': True,
        'table_id': 'supplierTable',
        'count': suppliers.count(),
        'count_noun': 'supplier',
        'create_url': reverse('admin_panel:supplier_create'),
        'create_label': 'Create Supplier',
        'bulk_action_url': reverse('admin_panel:bulk_supplier_action'),
        'empty_text': 'No suppliers found.',
    })


@admin_required
def supplier_create(request):
    """Admin manually creates a supplier account."""
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()
        email = request.POST.get('email', '').strip()
        first_name = request.POST.get('first_name', '').strip()
        phone = request.POST.get('phone', '').strip()
        company_name = request.POST.get('company_name', '').strip()
        category = request.POST.get('category', 'other')

        if not username or not password or not company_name:
            messages.error(request, 'Username, password and company name are required.')
            return render(request, 'admin_panel/supplier_create.html', {
                'category_choices': Supplier.CATEGORY_CHOICES,
                'post': request.POST,
            })

        from accounts.utils import is_valid_username
        if not is_valid_username(username):
            messages.error(request, 'Username must be a valid mobile number or email ID.')
            return render(request, 'admin_panel/supplier_create.html', {
                'category_choices': Supplier.CATEGORY_CHOICES,
                'post': request.POST,
            })

        if User.objects.filter(username=username).exists():
            messages.error(request, f'Username "{username}" already exists.')
            return render(request, 'admin_panel/supplier_create.html', {
                'category_choices': Supplier.CATEGORY_CHOICES,
                'post': request.POST,
            })

        user = User.objects.create_user(
            username=username,
            password=password,
            email=email,
            first_name=first_name,
            role='supplier',
            phone=phone,
        )
        Supplier.objects.create(
            user=user,
            company_name=company_name,
            category=category,
            status='approved',
        )
        messages.success(request, f'Supplier "{company_name}" created and approved.')
        return redirect('admin_panel:supplier_list')

    return render(request, 'admin_panel/supplier_create.html', {
        'category_choices': Supplier.CATEGORY_CHOICES,
    })


@admin_required
def supplier_detail(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    products = Product.objects.filter(supplier=supplier, is_deleted=False).prefetch_related('variants')
    return render(request, 'admin_panel/supplier_detail.html', {'supplier': supplier, 'products': products})


@admin_required
@require_POST
def supplier_suspend(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    supplier.status = 'suspended'
    supplier.save()
    from lakrish_mart.activity_logger import act
    act.user('suspended', user=supplier.user, by=request.user, detail=f'Supplier: {supplier.company_name}')
    messages.warning(request, f'{supplier.company_name} suspended.')
    return redirect('admin_panel:supplier_list')


@admin_required
@require_POST
def supplier_activate(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    supplier.status = 'approved'
    supplier.save()
    from lakrish_mart.activity_logger import act
    act.user('activated', user=supplier.user, by=request.user, detail=f'Supplier: {supplier.company_name}')
    messages.success(request, f'{supplier.company_name} activated.')
    return redirect('admin_panel:supplier_list')


# ── Commission Rules ──────────────────────────────────────────────────────────

@admin_required
def commission_rules(request):
    rules = CommissionRule.objects.select_related('supplier', 'product').order_by('rule_type')
    suppliers = Supplier.objects.filter(status='approved')
    products = Product.objects.filter(status='approved').select_related('supplier')
    return render(request, 'admin_panel/commission_rules.html', {
        'rules': rules,
        'suppliers': suppliers,
        'products': products,
        'type_choices': CommissionRule.TYPE_CHOICES,
    })


@admin_required
def commission_rule_add(request):
    if request.method == 'POST':
        rule_type = request.POST.get('rule_type')
        supplier_id = request.POST.get('supplier') or None
        product_id = request.POST.get('product') or None
        rate = request.POST.get('rate') or 0
        fixed_amount = request.POST.get('fixed_amount') or 0

        CommissionRule.objects.create(
            rule_type=rule_type,
            supplier_id=supplier_id,
            product_id=product_id,
            rate=rate,
            fixed_amount=fixed_amount,
        )
        messages.success(request, 'Commission rule added.')
    return redirect('admin_panel:commission_rules')


@admin_required
@require_POST
def commission_rule_delete(request, pk):
    """Retire a rule without destroying pricing history."""
    rule = get_object_or_404(CommissionRule, pk=pk)
    rule.is_active = False
    rule.save(update_fields=['is_active'])
    messages.success(request, 'Commission rule retired. It can be re-enabled later.')
    return redirect('admin_panel:commission_rules')


@admin_required
@require_POST
def commission_rule_toggle(request, pk):
    rule = get_object_or_404(CommissionRule, pk=pk)
    rule.is_active = not rule.is_active
    rule.save()
    return redirect('admin_panel:commission_rules')


# ── Stock Adjustment ──────────────────────────────────────────────────────────

@admin_required
def stock_adjust(request):
    """Admin stock view (read-only). Product-grouped, reusing the same table
    layout as the supplier "My Products" page. Admin cannot adjust stock here —
    stock changes happen at each tier via restock deliveries (see kb/04)."""
    from products.stock_display import annotate_products_for_stock

    supplier_id = request.GET.get('supplier')
    category_id = request.GET.get('category')
    # Default: only ACTIVE (approved) suppliers' products. ?show_suspended=1
    # includes suspended suppliers' products too.
    show_suspended = request.GET.get('show_suspended') == '1'
    suppliers = Supplier.objects.filter(status='approved').order_by('company_name')

    # Shared-catalogue products only (shop-private own-products are not
    # marketplace stock). Grouped by product so the shared table can render.
    products = (Product.objects.filter(is_deleted=False)
                .select_related('supplier', 'category')
                .prefetch_related('variants', 'variant_types')
                .order_by('supplier__company_name', 'name'))
    if not show_suspended:
        products = products.filter(supplier__status='approved')
    if supplier_id:
        products = products.filter(supplier_id=supplier_id)
    if category_id:
        products = products.filter(category_id=category_id)

    from products.stock_display import attach_downstream_detail
    products = annotate_products_for_stock(list(products))
    attach_downstream_detail(products)

    return render(request, 'admin_panel/stock_adjust.html', {
        'products': products,
        'suppliers': suppliers,
        'categories': Category.objects.all().order_by('name'),
        'selected_supplier': supplier_id,
        'selected_category': category_id,
        'show_suspended': show_suspended,
    })


# ── Marketplace / Shop (Admin View) ──────────────────────────────────────────

@admin_required
def marketplace(request):
    """View-only shop for admin to see approved products and their supplier prices."""
    products = Product.objects.filter(is_deleted=False, status='approved').select_related('supplier', 'category').prefetch_related('variants')
    
    # Filter logic
    search = request.GET.get('q', '').strip()
    category_id = request.GET.get('category', '')
    if search:
        products = products.filter(name__icontains=search)
    if category_id:
        products = products.filter(category_id=category_id)
        
    categories = Category.objects.all()
    
    return render(request, 'admin_panel/marketplace.html', {
        'products': products,
        'categories': categories,
        'search': search,
        'category_filter': category_id,
    })


# ── Product Management ────────────────────────────────────────────────────────

@admin_required
def product_list(request):
    show_deleted = request.GET.get('deleted') == '1'
    sort = request.GET.get('sort', 'name')
    search = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', '')
    supplier_filter = request.GET.get('supplier', '')
    category_filter = request.GET.get('category', '')

    SORT_MAP = {
        'name': 'name', '-name': '-name',
        'supplier': 'supplier__company_name', '-supplier': '-supplier__company_name',
        'category': 'category__name', '-category': '-category__name',
        'status': 'status', '-status': '-status',
        'created': 'created_at', '-created': '-created_at',
    }
    order_by = SORT_MAP.get(sort, 'name')
    products = Product.objects.select_related('supplier', 'category').prefetch_related('variants')
    if show_deleted:
        products = products.filter(is_deleted=True)
    else:
        products = products.filter(is_deleted=False)
    if search:
        products = products.filter(name__icontains=search)
    if status_filter:
        products = products.filter(status=status_filter)
    if supplier_filter:
        products = products.filter(supplier_id=supplier_filter)
    if category_filter:
        products = products.filter(category_id=category_filter)
    products = products.order_by(order_by)
    deleted_count = Product.objects.filter(is_deleted=True).count()
    pending_count = Product.objects.filter(status='pending', is_deleted=False).count()
    return render(request, 'admin_panel/product_list.html', {
        'products': products,
        'show_deleted': show_deleted,
        'deleted_count': deleted_count,
        'pending_count': pending_count,
        'sort': sort,
        'search': search,
        'status_filter': status_filter,
        'supplier_filter': supplier_filter,
        'category_filter': category_filter,
        'suppliers': Supplier.objects.filter(status='approved').order_by('company_name'),
        'categories': Category.objects.all().order_by('name'),
        'status_choices': Product.STATUS_CHOICES,
    })


@admin_required
@require_POST
def product_restore(request, pk):
    product = get_object_or_404(Product, pk=pk, is_deleted=True)
    product.restore()
    messages.success(request, f'"{product.name}" restored.')
    return redirect('admin_panel:product_list')


@admin_required
@require_POST
def product_approve(request, pk):
    product = get_object_or_404(Product, pk=pk)
    product.status = 'approved'

    supplier_user = product.supplier.user
    supplier_is_distributor = 'distributor' in supplier_user.get_roles_list()
    has_own_distributor = False
    if supplier_is_distributor:
        try:
            own_dist = supplier_user.distributor_profile
            has_own_distributor = True
            product.charges_confirmed = True
        except Exception:
            pass

    product.save()

    # Auto-list for the supplier's own distributor profile
    if has_own_distributor:
        try:
            _auto_list_product_for_single_distributor(product, own_dist)
        except Exception:
            pass

    # Only auto-list for OTHER distributors if this supplier does NOT have their own store
    # (if they have their own store, admin must explicitly choose to share with others)
    # Never share a "sell only in my own store" product anywhere else.
    auto_listed = 0
    if not has_own_distributor and not product.self_distribute_only:
        auto_listed = _auto_list_product_for_distributors(product)

    msg = f'"{product.name}" approved.'
    if product.self_distribute_only:
        msg += ' This product is restricted to the supplier\'s own store only.'
    elif has_own_distributor:
        msg += ' Listed in supplier\'s own distributor store only.'
    if auto_listed:
        msg += f' Auto-listed for {auto_listed} other distributor(s).'
    messages.success(request, msg)
    # Activity log
    try:
        from lakrish_mart.activity_logger import act as _act
        _act.product('approved', product=product, by=request.user,
                     detail=f'auto_listed={auto_listed}')
    except Exception:
        pass
    return redirect('admin_panel:product_list')


def _auto_list_product_for_single_distributor(product, dist):
    """Auto-list a product for a specific distributor using their margin settings."""
    from decimal import Decimal
    import re
    from distributors.services import apply_max_percent_cap
    from stockist.models import get_stockist_price, stockist_for, Stockist as StockistModel
    mode = dist.auto_list_margin_mode
    rate = Decimal(str(dist.auto_list_margin_value or 0))
    cap_percent = dist.auto_list_margin_cap_percent
    active_stockist = stockist_for(dist)

    dp, _ = DistributorProduct.objects.get_or_create(
        distributor=dist, product=product,
        defaults={'is_listed': True, 'margin_mode': mode, 'margin_percent': rate, 'margin_is_manual': False}
    )
    dp.is_listed = True
    dp.margin_mode = mode
    dp.margin_percent = rate
    dp.margin_is_manual = False
    dp.save()

    for variant in product.variants.filter(is_active=True):
        stockist_price, _ = get_stockist_price(product, variant, active_stockist)
        base_price = stockist_price or variant.selling_price
        if mode == 'per_unit' and variant.unit_grams:
            label = variant.label.strip().lower()
            m = re.match(r'^([\d.]+)\s*(kg|g|ml|l)?$', label)
            if m:
                num = float(m.group(1))
                unit = m.group(2) or 'g'
                weight_kg = num if unit in ('kg', 'l') else num / 1000
            else:
                weight_kg = 1.0
            markup = float(rate) * weight_kg
            sell = base_price + Decimal(str(round(markup, 2)))
            sell = apply_max_percent_cap(base_price, sell, cap_percent)
        else:
            sell = base_price * (1 + rate / 100)

        if product.show_mrp and variant.mrp:
            sell = min(sell, variant.mrp)
        sell = max(sell, base_price)

        DistributorVariantPrice.objects.update_or_create(
            distributor=dist, variant=variant,
            defaults={
                'base_price': base_price,
                'selling_price': sell,
                'is_active': True,
                'price_stale': False,
            }
        )


def _auto_list_product_for_distributors(product):
    """Auto-list a newly approved product for all distributors with auto_list enabled.
    Only lists if the distributor has stock for at least one variant of the product,
    OR if the distributor uses supplier-fulfillment (no pre-stock required).
    """
    from decimal import Decimal
    from distributors.models import DistributorStock
    from distributors.services import apply_max_percent_cap
    from stockist.models import get_stockist_price, stockist_for, Stockist as StockistModel
    import re
    # "Sell only in my own store" products must never be auto-listed to others.
    if getattr(product, 'self_distribute_only', False):
        return 0
    count = 0
    for dist in Distributor.objects.filter(status='active', auto_list_new_products=True):
        # Stage 7: each shop's price floor comes from ITS OWN chain's stockist.
        active_stockist = stockist_for(dist)
        # Safety check: only auto-list if distributor has stock or uses supplier fulfillment
        has_stock = DistributorStock.objects.filter(
            distributor=dist,
            variant__product=product,
            quantity__gt=0,
        ).exists()
        uses_supplier_fulfillment = not getattr(dist, 'requires_pre_stock', False)
        if not has_stock and not uses_supplier_fulfillment:
            continue  # Skip — distributor has no stock and requires pre-stocking

        mode = dist.auto_list_margin_mode
        rate = dist.auto_list_margin_value
        cap_percent = dist.auto_list_margin_cap_percent

        dp, _ = DistributorProduct.objects.get_or_create(
            distributor=dist, product=product,
            defaults={'is_listed': True, 'margin_mode': mode, 'margin_percent': rate, 'margin_is_manual': False}
        )
        dp.is_listed = True
        dp.margin_mode = mode
        dp.margin_percent = rate
        dp.margin_is_manual = False
        dp.save()

        for variant in product.variants.filter(is_active=True):
            stockist_price, _ = get_stockist_price(product, variant, active_stockist)
            base_price = stockist_price or variant.selling_price
            # Calculate selling price based on margin mode
            if mode == 'per_unit' and variant.unit_grams:
                label = variant.label.strip().lower()
                m = re.match(r'^([\d.]+)\s*(kg|g|ml|l)?$', label)
                if m:
                    num = float(m.group(1))
                    unit = m.group(2) or 'g'
                    weight_kg = num if unit in ('kg', 'l') else num / 1000
                else:
                    weight_kg = 1.0
                markup = float(rate) / 2 if weight_kg < 1 else float(rate) * weight_kg
                sell = base_price + Decimal(str(markup))
                sell = apply_max_percent_cap(base_price, sell, cap_percent)
            else:
                sell = base_price * (1 + rate / 100)

            if product.show_mrp and variant.mrp:
                sell = min(sell, variant.mrp)
            sell = max(sell, base_price)

            DistributorVariantPrice.objects.update_or_create(
                distributor=dist, variant=variant,
                defaults={
                    'base_price': base_price,
                    'selling_price': sell,
                    'is_active': True,
                    'price_stale': False,
                }
            )
        count += 1
    return count


@admin_required
@require_POST
def product_reject(request, pk):
    product = get_object_or_404(Product, pk=pk)
    product.status = 'rejected'
    product.save()
    try:
        from lakrish_mart.activity_logger import act as _act
        _act.product('rejected', product=product, by=request.user)
    except Exception:
        pass
    messages.warning(request, f'"{product.name}" rejected.')
    return redirect('admin_panel:product_list')


@admin_required
@require_POST
def product_delete(request, pk):
    product = get_object_or_404(Product, pk=pk, is_deleted=False)
    product.soft_delete()
    messages.success(request, f'"{product.name}" deleted.')
    return redirect('admin_panel:product_list')


# ── Order Management (Read-Only — Admin does not manage orders) ───────────────

@admin_required
def order_list(request):
    """Admin can view all orders for reporting purposes only."""
    sort = request.GET.get('sort', '-created_at')
    status_filter = request.GET.get('status', '')
    SORT_MAP = {
        'id': 'id', '-id': '-id',
        'total': 'total_amount', '-total': '-total_amount',
        'status': 'status', '-status': '-status',
        'date': 'created_at', '-date': '-created_at',
        'customer': 'customer__username', '-customer': '-customer__username',
    }
    order_by = SORT_MAP.get(sort, '-created_at')
    orders = Order.objects.select_related('customer', 'distributor').prefetch_related('items').order_by(order_by)
    if status_filter:
        orders = orders.filter(status=status_filter)
    return render(request, 'admin_panel/order_list.html', {
        'orders': orders,
        'sort': sort,
        'status_filter': status_filter,
        'status_choices': Order.STATUS_CHOICES,
        'readonly': True,
    })


@admin_required
def order_detail(request, pk):
    """Admin can view order details for reporting purposes only."""
    from django.db.models import Prefetch
    from orders.models import OrderItem
    order = get_object_or_404(
        Order.objects.prefetch_related(Prefetch(
            'items',
            queryset=OrderItem.objects.select_related(
                'product', 'product__supplier', 'variant', 'supplier_shipment'),
        )),
        pk=pk,
    )
    return render(request, 'admin_panel/order_detail.html', {'order': order, 'readonly': True})


@admin_required
def update_order_status(request, pk):
    """Admin updates order status."""
    order = get_object_or_404(Order, pk=pk)
    if request.method == 'POST':
        new_status = request.POST.get('status', '').strip()
        valid_statuses = [s[0] for s in order.STATUS_CHOICES]
        if new_status in valid_statuses:
            order.status = new_status
            order.save(update_fields=['status'])
            messages.success(request, f'Order #{order.pk} status updated to {new_status}.')
        else:
            messages.error(request, 'Invalid status.')
    return redirect('admin_panel:order_detail', pk=pk)


# ── Category Management ───────────────────────────────────────────────────────

@admin_required
def category_list(request):
    categories = Category.objects.all()
    return render(request, 'admin_panel/category_list.html', {'categories': categories})


@admin_required
def category_add(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        slug = request.POST.get('slug', '').strip()
        if name and slug:
            Category.objects.create(name=name, slug=slug)
            messages.success(request, 'Category added.')
        return redirect('admin_panel:category_list')
    return render(request, 'admin_panel/category_form.html')


@admin_required
@require_POST
def category_delete(request, pk):
    """Block permanent category deletion until an archive lifecycle exists."""
    cat = get_object_or_404(Category, pk=pk)
    messages.info(
        request,
        f'Category "{cat.name}" was retained. Categories are not permanently deleted; '
        'remove or reassign products instead.'
    )
    return redirect('admin_panel:category_list')


# ── Distributor Management ────────────────────────────────────────────────────

@admin_required
def distributor_list(request):
    sort = request.GET.get('sort', 'company_name')
    status_filter = request.GET.get('status', 'active')
    search = request.GET.get('q', '').strip()
    
    # Column-specific searches
    search_company = request.GET.get('company', '').strip()
    search_area = request.GET.get('area', '').strip()
    search_username = request.GET.get('username', '').strip()
    search_customers = request.GET.get('customers', '').strip()
    search_orders = request.GET.get('orders', '').strip()
    search_delivery = request.GET.get('delivery', '').strip()
    search_status = request.GET.get('status_col', '').strip()
    
    SORT_MAP = {
        'name': 'company_name', '-name': '-company_name',
        'area': 'area', '-area': '-area',
        'status': 'status', '-status': '-status',
        'customers': 'customer_count', '-customers': '-customer_count',
        'orders': 'order_count', '-orders': '-order_count',
        'delivery': 'delivery_charge', '-delivery': '-delivery_charge',
    }
    order_by = SORT_MAP.get(sort, 'company_name')
    _base = Distributor.objects.select_related('user').annotate(
        customer_count=Count('customers', filter=Q(customers__is_deleted=False), distinct=True),
        order_count=Count('orders', distinct=True),
    ).order_by(order_by)
    if status_filter == 'deleted':
        from admin_panel.purge import purge_due_accounts
        purge_due_accounts()
        distributors = _base.filter(user__is_deleted=True, user__is_purged=False)
    else:
        distributors = _base.filter(user__is_deleted=False)
        if status_filter and status_filter != 'all':
            distributors = distributors.filter(status=status_filter)
    
    # Main search (searches all fields)
    if search:
        distributors = (distributors.filter(company_name__icontains=search) |
                       distributors.filter(user__username__icontains=search) |
                       distributors.filter(area__icontains=search))
    
    # Column-specific searches
    if search_company:
        distributors = distributors.filter(company_name__icontains=search_company)
    if search_area:
        distributors = distributors.filter(area__icontains=search_area)
    if search_username:
        distributors = distributors.filter(user__username__icontains=search_username)
    if search_customers:
        distributors = distributors.filter(customer_count__icontains=search_customers)
    if search_orders:
        distributors = distributors.filter(order_count__icontains=search_orders)
    if search_delivery:
        distributors = distributors.filter(delivery_charge__icontains=search_delivery)
    if search_status:
        distributors = distributors.filter(status__icontains=search_status)

    active_count = Distributor.objects.filter(status='active', user__is_deleted=False).count()
    suspended_count = Distributor.objects.filter(status='suspended', user__is_deleted=False).count()
    deleted_count = Distributor.objects.filter(user__is_deleted=True, user__is_purged=False).count()

    from django.urls import reverse
    return render(request, 'admin_panel/distributor_list.html', {
        'distributors': distributors,
        'sort': sort,
        'status_filter': status_filter,
        'active_count': active_count,
        'suspended_count': suspended_count,
        'deleted_count': deleted_count,
        'search': search,
        # Common partner-list layout config:
        'page_title': 'Marketplaces',
        'active_nav': 'distributor_list',
        'show_tabs': True,
        'tab_active_value': 'active',
        'show_search': False,  # uses per-column instant search instead
        'table_id': 'distTable',
        'create_url': reverse('admin_panel:distributor_create'),
        'create_label': 'Create Marketplace',
        'bulk_action_url': reverse('admin_panel:bulk_distributor_action'),
        'empty_text': 'No marketplaces found.',
    })


@admin_required
def distributor_create(request):
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()
        email = request.POST.get('email', '').strip()
        first_name = request.POST.get('first_name', '').strip()
        phone = request.POST.get('phone', '').strip()
        company_name = request.POST.get('company_name', '').strip()
        area = request.POST.get('area', '').strip()
        delivery_charge = request.POST.get('delivery_charge', 0)
        free_delivery_above = request.POST.get('free_delivery_above', 0)
        default_margin = request.POST.get('default_margin_percent', 0)

        if not username or not password or not company_name:
            messages.error(request, 'Username, password and company name are required.')
            return render(request, 'admin_panel/distributor_create.html', {'post': request.POST})

        from accounts.utils import is_valid_username
        if not is_valid_username(username):
            messages.error(request, 'Username must be a valid mobile number or email ID.')
            return render(request, 'admin_panel/distributor_create.html', {'post': request.POST})

        if User.objects.filter(username=username).exists():
            messages.error(request, f'Username "{username}" already exists.')
            return render(request, 'admin_panel/distributor_create.html', {'post': request.POST})

        user = User.objects.create_user(
            username=username, password=password, email=email,
            first_name=first_name, role='distributor', phone=phone,
        )
        Distributor.objects.create(
            user=user, company_name=company_name, area=area,
            delivery_charge=delivery_charge,
            free_delivery_above=free_delivery_above,
            default_margin_percent=default_margin,
            status='active',
        )
        messages.success(request, f'Marketplace "{company_name}" created.')
        return redirect('admin_panel:distributor_list')

    return render(request, 'admin_panel/distributor_create.html', {})


@admin_required
def distributor_detail(request, pk):
    distributor = get_object_or_404(Distributor, pk=pk)
    customers = User.objects.filter(distributor=distributor, role='customer', is_deleted=False)
    orders = Order.objects.filter(distributor=distributor).order_by('-created_at')[:50]
    return render(request, 'admin_panel/distributor_detail.html', {
        'distributor': distributor, 
        'customers': customers, 
        'orders': orders
    })


@admin_required
@require_POST
def distributor_toggle(request, pk):
    dist = get_object_or_404(Distributor, pk=pk)
    new_status = 'suspended' if dist.status == 'active' else 'active'
    dist.status = new_status
    dist.save()

    from lakrish_mart.activity_logger import act
    act.user('suspended' if new_status == 'suspended' else 'activated',
             user=dist.user, by=request.user, detail=f'Marketplace: {dist.company_name}')
    if new_status == 'suspended':
        customer_count = dist.customers.filter(is_deleted=False).count()
        pending_orders = dist.orders.filter(status='pending').count()
        msg = f'{dist.company_name} suspended.'
        if customer_count:
            msg += f' {customer_count} customer(s) will not be able to shop until reactivated.'
        if pending_orders:
            msg += f' {pending_orders} pending order(s) need attention.'
        messages.warning(request, msg)
    else:
        messages.success(request, f'{dist.company_name} reactivated. Customers can shop again.')

    return redirect('admin_panel:distributor_list')


@admin_required
def distributor_delete(request, pk):
    """Archive a Marketplace without deleting customers, orders, or stock data."""
    dist = get_object_or_404(Distributor, pk=pk)

    if request.method == 'POST':
        dist.status = 'suspended'
        dist.save(update_fields=['status'])
        messages.success(
            request,
            f'Marketplace "{dist.company_name}" archived (suspended). '
            'Customers, orders, pricing, and inventory were preserved; reactivate it to restore access.'
        )
        return redirect('admin_panel:distributor_list')

    # GET: show an explicit, non-destructive archive confirmation.
    return render(request, 'admin_panel/distributor_confirm_delete.html', {'distributor': dist})


# ── Wholesale Distributors (code: Wholesaler; UI label: "Distributor") ────────

@admin_required
def wholesaler_list(request):
    """Manage the wholesale Distributor tier (one Distributor supplies many
    Marketplaces). Uses the common partner-list layout."""
    from django.urls import reverse

    sort = request.GET.get('sort', 'company_name')
    status_filter = request.GET.get('status', 'active')
    search = request.GET.get('q', '').strip()
    SORT_MAP = {
        'name': 'company_name', '-name': '-company_name',
        'area': 'area', '-area': '-area',
        'status': 'status', '-status': '-status',
        'marketplaces': 'ent_count', '-marketplaces': '-ent_count',
    }
    order_by = SORT_MAP.get(sort, 'company_name')
    _base = Wholesaler.objects.select_related('user', 'stockist').annotate(
        ent_count=Count('enterprises', distinct=True),
    ).order_by(order_by)
    if status_filter == 'deleted':
        from admin_panel.purge import purge_due_accounts
        purge_due_accounts()
        wholesalers = _base.filter(user__is_deleted=True, user__is_purged=False)
    else:
        wholesalers = _base.filter(user__is_deleted=False)
        if status_filter and status_filter != 'all':
            wholesalers = wholesalers.filter(status=status_filter)
    if search:
        wholesalers = (wholesalers.filter(company_name__icontains=search) |
                       wholesalers.filter(user__username__icontains=search) |
                       wholesalers.filter(area__icontains=search))

    active_count = Wholesaler.objects.filter(status='active', user__is_deleted=False).count()
    suspended_count = Wholesaler.objects.filter(status='suspended', user__is_deleted=False).count()
    deleted_count = Wholesaler.objects.filter(user__is_deleted=True, user__is_purged=False).count()

    return render(request, 'admin_panel/wholesaler_list.html', {
        'wholesalers': wholesalers,
        'sort': sort,
        'status_filter': status_filter,
        'search': search,
        'active_count': active_count,
        'suspended_count': suspended_count,
        'deleted_count': deleted_count,
        'unassigned_enterprises': Distributor.objects.filter(
            wholesaler__isnull=True, user__is_deleted=False
        ).count(),
        # Common partner-list layout config:
        'page_title': 'Distributors',
        'active_nav': 'wholesaler_list',
        'show_tabs': True,
        'tab_active_value': 'active',
        'show_search': True,
        'table_id': 'wholesalerTable',
        'count': wholesalers.count(),
        'count_noun': 'distributor',
        'create_url': reverse('admin_panel:wholesaler_create'),
        'create_label': 'New Distributor',
        'bulk_action_url': reverse('admin_panel:bulk_wholesaler_action'),
        'empty_text': 'No wholesale Distributors yet. Create one to supply your Marketplaces.',
    })


@admin_required
def wholesaler_create(request):
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()
        company_name = request.POST.get('company_name', '').strip()
        area = request.POST.get('area', '').strip()
        phone = request.POST.get('phone', '').strip()
        margin_mode = request.POST.get('margin_mode', 'per_quantity')
        margin_value = request.POST.get('margin_value', 0) or 0

        if not username or not password or not company_name:
            messages.error(request, 'Username, password and company name are required.')
            return render(request, 'admin_panel/wholesaler_create.html', {'post': request.POST})

        from accounts.utils import is_valid_username
        if not is_valid_username(username):
            messages.error(request, 'Username must be a valid mobile number or email ID.')
            return render(request, 'admin_panel/wholesaler_create.html', {'post': request.POST})
        if User.objects.filter(username=username).exists():
            messages.error(request, f'Username "{username}" already exists.')
            return render(request, 'admin_panel/wholesaler_create.html', {'post': request.POST})

        user = User.objects.create_user(
            username=username, password=password, role='wholesaler', phone=phone,
        )
        if margin_mode not in ('per_quantity', 'per_weight'):
            margin_mode = 'per_quantity'
        Wholesaler.objects.create(
            user=user, company_name=company_name, area=area,
            margin_mode=margin_mode, margin_value=margin_value, status='active',
        )
        messages.success(request, f'Distributor "{company_name}" created.')
        return redirect('admin_panel:wholesaler_detail', pk=user.wholesaler_profile.pk)

    return render(request, 'admin_panel/wholesaler_create.html', {})


@admin_required
def wholesaler_detail(request, pk):
    from stockist.models import Stockist
    w = get_object_or_404(Wholesaler, pk=pk)

    # Stage 7: assign the ONE stockist this Distributor buys from.
    if request.method == 'POST' and request.POST.get('action') == 'set_stockist':
        raw = request.POST.get('stockist_pk', '')
        if raw:
            w.stockist = get_object_or_404(Stockist, pk=raw)
            w.save(update_fields=['stockist'])
            messages.success(request, f'{w.company_name} now buys from {w.stockist.company_name}.')
        else:
            w.stockist = None
            w.save(update_fields=['stockist'])
            messages.success(request, f'{w.company_name} has no assigned stockist (uses the default).')
        return redirect('admin_panel:wholesaler_detail', pk=w.pk)

    # Marketplaces available to assign = those with no Distributor yet
    available = Distributor.objects.filter(
        wholesaler__isnull=True, user__is_deleted=False
    ).order_by('company_name')
    return render(request, 'admin_panel/wholesaler_detail.html', {
        'wholesaler': w,
        'enterprises': w.enterprises.select_related('user').order_by('company_name'),
        'available': available,
        'stockists': Stockist.objects.filter(status='active').order_by('company_name'),
    })


@admin_required
@require_POST
def wholesaler_toggle(request, pk):
    w = get_object_or_404(Wholesaler, pk=pk)
    w.status = 'suspended' if w.status == 'active' else 'active'
    w.save(update_fields=['status'])
    from lakrish_mart.activity_logger import act
    act.user('suspended' if w.status == 'suspended' else 'activated',
             user=w.user, by=request.user, detail=f'Distributor: {w.company_name}')
    messages.success(request, f'{w.company_name} is now {w.status}.')
    # Return to wherever the action was triggered (list or detail page).
    referer = request.META.get('HTTP_REFERER')
    if referer:
        return redirect(referer)
    return redirect('admin_panel:wholesaler_detail', pk=w.pk)


@admin_required
@require_POST
def wholesaler_assign(request, pk):
    """Assign or unassign an Marketplace to this wholesale Distributor."""
    w = get_object_or_404(Wholesaler, pk=pk)
    if request.method == 'POST':
        ent_pk = request.POST.get('enterprise_pk')
        action = request.POST.get('action', 'assign')
        ent = get_object_or_404(Distributor, pk=ent_pk)
        if action == 'unassign':
            ent.wholesaler = None
            ent.save(update_fields=['wholesaler'])
            messages.success(request, f'Removed "{ent.company_name}" from {w.company_name}.')
        else:
            ent.wholesaler = w
            ent.save(update_fields=['wholesaler'])
            messages.success(request, f'Assigned "{ent.company_name}" to {w.company_name}.')
    return redirect('admin_panel:wholesaler_detail', pk=w.pk)


@admin_required
def distributor_set_prices(request, pk):
    """Admin sets the price they charge the distributor for each variant."""
    dist = get_object_or_404(Distributor, pk=pk)
    products = Product.objects.filter(status='approved').prefetch_related('variants')
    dvp_map = {dvp.variant_id: dvp for dvp in DistributorVariantPrice.objects.filter(distributor=dist)}

    if request.method == 'POST':
        for key, val in request.POST.items():
            if key.startswith('base_price_') and val.strip():
                try:
                    vid = int(key.replace('base_price_', ''))
                    price = float(val)
                    dvp, _ = DistributorVariantPrice.objects.get_or_create(
                        distributor=dist, variant_id=vid,
                        defaults={'base_price': price, 'selling_price': price}
                    )
                    dvp.base_price = price
                    if dvp.selling_price < price:
                        dvp.selling_price = price
                    dvp.save()
                except (ValueError, IndexError):
                    pass
        messages.success(request, 'Admin prices saved.')
        return redirect('admin_panel:distributor_set_prices', pk=pk)

    products_data = []
    for p in products:
        variants_data = []
        for v in p.variants.filter(is_active=True):
            dvp = dvp_map.get(v.pk)
            variants_data.append({'variant': v, 'dvp': dvp})
        products_data.append({'product': p, 'variants': variants_data})

    return render(request, 'admin_panel/distributor_set_prices.html', {
        'dist': dist, 'products_data': products_data,
    })


# ── Reports ───────────────────────────────────────────────────────────────────

@admin_required
def reports(request):
    from django.db.models import Q, Avg
    from datetime import datetime, timedelta
    import json
    
    # Date range filter
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    
    # Base querysets
    orders_qs = Order.objects.all()
    if date_from:
        try:
            orders_qs = orders_qs.filter(created_at__gte=datetime.strptime(date_from, '%Y-%m-%d'))
        except ValueError:
            pass
    if date_to:
        try:
            orders_qs = orders_qs.filter(created_at__lte=datetime.strptime(date_to, '%Y-%m-%d').replace(hour=23, minute=59, second=59))
        except ValueError:
            pass
    
    role_ledger_summary = _role_ledger_summary(orders_qs)
    
    # Top Products by order count
    top_products = (
        Product.objects.filter(status='approved')
        .annotate(order_count=Count('orderitem')).order_by('-order_count')[:10]
    )
    
    # Sales by Status
    sales_by_status = orders_qs.values('status').annotate(
        count=Count('id'),
        total=Sum('total_amount')
    ).order_by('-count')
    
    # Sales by Distributor
    sales_by_distributor = orders_qs.values('distributor__company_name').annotate(
        count=Count('id'),
        total=Sum('total_amount')
    ).order_by('-total')[:10]
    
    # Revenue Over Time (last 30 days)
    from django.db.models.functions import TruncDate
    thirty_days_ago = timezone.now() - timedelta(days=30)
    daily_revenue = orders_qs.filter(
        created_at__gte=thirty_days_ago,
        status='delivered'
    ).annotate(
        day=TruncDate('created_at')
    ).values('day').annotate(
        revenue=Sum('total_amount'),
        orders=Count('id')
    ).order_by('day')
    
    # Overall Statistics
    total_orders = orders_qs.count()
    total_revenue = orders_qs.filter(status='delivered').aggregate(total=Sum('total_amount'))['total'] or 0
    avg_order_value = orders_qs.filter(status='delivered').aggregate(avg=Avg('total_amount'))['avg'] or 0
    pending_orders = orders_qs.filter(status='pending').count()
    
    # Customer Statistics
    total_customers = User.objects.filter(role='customer', is_deleted=False).count()
    customers_with_orders = User.objects.filter(
        role='customer', 
        is_deleted=False,
        orders__isnull=False
    ).distinct().count()
    
    # Product Statistics
    total_products = Product.objects.filter(status='approved', is_deleted=False).count()
    low_stock_count = ProductVariant.objects.shared().filter(stock__lt=10, stock__gt=0).count()
    out_of_stock_count = ProductVariant.objects.shared().filter(stock=0).count()
    
    # Top Customers by order value
    top_customers = User.objects.filter(
        role='customer',
        orders__status='delivered'
    ).annotate(
        total_spent=Sum('orders__total_amount'),
        order_count=Count('orders')
    ).order_by('-total_spent')[:10]
    
    # Prepare chart data
    chart_labels = [str(item['day']) for item in daily_revenue]
    chart_revenue = [float(item['revenue']) for item in daily_revenue]
    chart_orders = [item['orders'] for item in daily_revenue]
    
    context = {
        'role_ledger_summary': role_ledger_summary,
        'top_products': top_products,
        'sales_by_status': sales_by_status,
        'sales_by_distributor': sales_by_distributor,
        'total_orders': total_orders,
        'total_revenue': total_revenue,
        'avg_order_value': avg_order_value,
        'pending_orders': pending_orders,
        'total_customers': total_customers,
        'customers_with_orders': customers_with_orders,
        'total_products': total_products,
        'low_stock_count': low_stock_count,
        'out_of_stock_count': out_of_stock_count,
        'top_customers': top_customers,
        'chart_labels': json.dumps(chart_labels),
        'chart_revenue': json.dumps(chart_revenue),
        'chart_orders': json.dumps(chart_orders),
        'date_from': date_from,
        'date_to': date_to,
    }
    
    return render(request, 'admin_panel/reports.html', context)


@admin_required
def export_reports(request):
    """Export various reports in Excel or CSV format"""
    import openpyxl
    from django.http import HttpResponse
    from datetime import datetime, timedelta
    
    report_type = request.GET.get('type', 'summary')
    fmt = request.GET.get('fmt', 'xlsx')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    
    # Filter orders by date range
    orders_qs = Order.objects.all()
    if date_from:
        try:
            orders_qs = orders_qs.filter(created_at__gte=datetime.strptime(date_from, '%Y-%m-%d'))
        except ValueError:
            pass
    if date_to:
        try:
            orders_qs = orders_qs.filter(created_at__lte=datetime.strptime(date_to, '%Y-%m-%d').replace(hour=23, minute=59, second=59))
        except ValueError:
            pass
    
    if fmt == 'csv':
        import csv
        response = HttpResponse(content_type='text/csv')
        
        if report_type in ('ledger', 'commission'):
            response['Content-Disposition'] = 'attachment; filename="role_ledger_report.csv"'
            writer = csv.writer(response)
            writer.writerow(['Role', 'Party', 'Entry', 'Total', 'Status'])
            for row in _role_ledger_summary(orders_qs):
                party_name = row['distributor__company_name'] or row['stockist__company_name'] or 'Customer'
                writer.writerow([row['party_role'], party_name, row['entry_type'], row['total'], row['status']])
                
        elif report_type == 'products':
            response['Content-Disposition'] = 'attachment; filename="top_products_report.csv"'
            writer = csv.writer(response)
            writer.writerow(['Product', 'Category', 'Supplier', 'Orders', 'Status'])
            top_products = Product.objects.filter(status='approved').annotate(order_count=Count('orderitem')).order_by('-order_count')[:50]
            for p in top_products:
                writer.writerow([p.name, p.category.name if p.category else '', p.supplier.company_name, p.order_count, p.status])
                
        elif report_type == 'customers':
            response['Content-Disposition'] = 'attachment; filename="top_customers_report.csv"'
            writer = csv.writer(response)
            writer.writerow(['Customer', 'Email', 'Phone', 'Total Spent', 'Orders', 'Distributor'])
            top_customers = User.objects.filter(role='customer', orders__status='delivered').annotate(
                total_spent=Sum('orders__total_amount'),
                order_count=Count('orders')
            ).order_by('-total_spent')[:50]
            for c in top_customers:
                writer.writerow([
                    c.get_full_name() or c.username,
                    c.email,
                    c.phone or '',
                    c.total_spent,
                    c.order_count,
                    c.distributor.company_name if c.distributor else 'Not Assigned'
                ])
                
        elif report_type == 'distributors':
            response['Content-Disposition'] = 'attachment; filename="distributor_sales_report.csv"'
            writer = csv.writer(response)
            writer.writerow(['Distributor', 'Total Orders', 'Total Sales', 'Status'])
            sales_by_distributor = orders_qs.values('distributor__company_name', 'distributor__status').annotate(
                count=Count('id'),
                total=Sum('total_amount')
            ).order_by('-total')
            for row in sales_by_distributor:
                writer.writerow([row['distributor__company_name'] or 'N/A', row['count'], row['total'], row['distributor__status'] or ''])
                
        elif report_type == 'inventory':
            response['Content-Disposition'] = 'attachment; filename="inventory_report.csv"'
            writer = csv.writer(response)
            writer.writerow(['Product', 'Variant', 'Supplier', 'Stock', 'Price', 'Status'])
            variants = ProductVariant.objects.shared().select_related('product__supplier').order_by('stock', 'product__name')
            for v in variants:
                writer.writerow([
                    v.product.name,
                    v.label,
                    v.product.supplier.company_name,
                    v.stock,
                    v.selling_price,
                    'Out of Stock' if v.stock == 0 else ('Low Stock' if v.stock < 10 else 'In Stock')
                ])
        else:  # summary
            response['Content-Disposition'] = 'attachment; filename="sales_summary_report.csv"'
            writer = csv.writer(response)
            writer.writerow(['Metric', 'Value'])
            total_orders = orders_qs.count()
            total_revenue = orders_qs.filter(status='delivered').aggregate(total=Sum('total_amount'))['total'] or 0
            writer.writerow(['Total Orders', total_orders])
            writer.writerow(['Total Revenue', total_revenue])
            writer.writerow(['Pending Orders', orders_qs.filter(status='pending').count()])
            writer.writerow(['Delivered Orders', orders_qs.filter(status='delivered').count()])
            writer.writerow(['Cancelled Orders', orders_qs.filter(status='cancelled').count()])
            
        return response
    
    # Excel format
    wb = openpyxl.Workbook()
    ws = wb.active
    
    if report_type in ('ledger', 'commission'):
        ws.title = 'Role Ledger Report'
        ws.append(['Role', 'Party', 'Entry', 'Total', 'Status'])
        for row in _role_ledger_summary(orders_qs):
            ws.append([row['party_role'], row['party_name'], row['entry_type'], float(row['total'] or 0), row['status']])
        filename = 'role_ledger_report.xlsx'
        
    elif report_type == 'products':
        ws.title = 'Top Products'
        ws.append(['Product', 'Category', 'Supplier', 'Orders', 'Status'])
        top_products = Product.objects.filter(status='approved').annotate(order_count=Count('orderitem')).order_by('-order_count')[:50]
        for p in top_products:
            ws.append([p.name, p.category.name if p.category else '', p.supplier.company_name, p.order_count, p.status])
        filename = 'top_products_report.xlsx'
        
    elif report_type == 'customers':
        ws.title = 'Top Customers'
        ws.append(['Customer', 'Email', 'Phone', 'Total Spent', 'Orders', 'Distributor'])
        top_customers = User.objects.filter(role='customer', orders__status='delivered').annotate(
            total_spent=Sum('orders__total_amount'),
            order_count=Count('orders')
        ).order_by('-total_spent')[:50]
        for c in top_customers:
            ws.append([
                c.get_full_name() or c.username,
                c.email,
                c.phone or '',
                float(c.total_spent),
                c.order_count,
                c.distributor.company_name if c.distributor else 'Not Assigned'
            ])
        filename = 'top_customers_report.xlsx'
        
    elif report_type == 'distributors':
        ws.title = 'Distributor Sales'
        ws.append(['Distributor', 'Total Orders', 'Total Sales', 'Status'])
        sales_by_distributor = orders_qs.values('distributor__company_name', 'distributor__status').annotate(
            count=Count('id'),
            total=Sum('total_amount')
        ).order_by('-total')
        for row in sales_by_distributor:
            ws.append([row['distributor__company_name'] or 'N/A', row['count'], float(row['total'] or 0), row['distributor__status'] or ''])
        filename = 'distributor_sales_report.xlsx'
        
    elif report_type == 'inventory':
        ws.title = 'Inventory Report'
        ws.append(['Product', 'Variant', 'Supplier', 'Stock', 'Price', 'Status'])
        variants = ProductVariant.objects.shared().select_related('product__supplier').order_by('stock', 'product__name')
        for v in variants:
            ws.append([
                v.product.name,
                v.label,
                v.product.supplier.company_name,
                v.stock,
                float(v.selling_price),
                'Out of Stock' if v.stock == 0 else ('Low Stock' if v.stock < 10 else 'In Stock')
            ])
        filename = 'inventory_report.xlsx'
    else:  # summary
        ws.title = 'Sales Summary'
        ws.append(['Metric', 'Value'])
        total_orders = orders_qs.count()
        total_revenue = orders_qs.filter(status='delivered').aggregate(total=Sum('total_amount'))['total'] or 0
        ws.append(['Total Orders', total_orders])
        ws.append(['Total Revenue', float(total_revenue)])
        ws.append(['Pending Orders', orders_qs.filter(status='pending').count()])
        ws.append(['Delivered Orders', orders_qs.filter(status='delivered').count()])
        ws.append(['Cancelled Orders', orders_qs.filter(status='cancelled').count()])
        filename = 'sales_summary_report.xlsx'
    
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response


def _role_ledger_summary(orders_qs):
    order_ids = orders_qs.values_list('id', flat=True)
    return list(
        PaymentLedgerEntry.objects.filter(order_id__in=order_ids)
        .values('party_role', 'entry_type', 'status', 'distributor__company_name', 'stockist__company_name')
        .annotate(total=Sum('amount'))
        .order_by('party_role', 'entry_type', 'status')
    )


# ── User Management ───────────────────────────────────────────────────────────

@admin_required
def user_list(request):
    role = request.GET.get('role', '')
    search = request.GET.get('q', '').strip()
    show_deleted = request.GET.get('deleted') == '1'
    unlinked = request.GET.get('unlinked') == '1'
    sort = request.GET.get('sort', '-date_joined')

    # Quick inline actions
    if request.method == 'POST':
        action = request.POST.get('action')
        user_id = request.POST.get('user_id')
        if user_id:
            target = get_object_or_404(User, pk=user_id)
            if action == 'delete' and not target.is_superuser:
                target.soft_delete(deleted_by=request.user.username)
                from lakrish_mart.activity_logger import act
                act.user('soft_deleted', user=target, by=request.user, detail='User')
                messages.success(request, f'User "{target.username}" deleted.')
            elif action == 'assign_distributor':
                dist_id = request.POST.get('distributor_id') or None
                target.distributor_id = dist_id
                target.save(update_fields=['distributor'])
                messages.success(request, f'Marketplace assigned to "{target.username}".')
        return redirect(request.get_full_path())

    SORT_MAP = {
        'username': 'username', '-username': '-username',
        'name': 'first_name', '-name': '-first_name',
        'role': 'role', '-role': '-role',
        'date_joined': 'date_joined', '-date_joined': '-date_joined',
    }
    order_by = SORT_MAP.get(sort, '-date_joined')

    # Deleted users are hidden by the default manager (ActiveUserManager), so
    # the deleted view MUST query all_objects — otherwise ?deleted=1 is always
    # empty and soft-deleted accounts become invisible everywhere.
    base_manager = User.all_objects if show_deleted else User.objects
    users = base_manager.select_related(
        'supplier_profile', 'distributor_profile', 'distributor'
    ).order_by(order_by)

    if show_deleted:
        users = users.filter(is_deleted=True, is_purged=False)
    else:
        users = users.filter(is_deleted=False)

    if role == 'multi':
        users = users.filter(roles__contains=',')
    elif role:
        users = users.filter(Q(role=role) | Q(roles__icontains=role))
    if search:
        users = (users.filter(username__icontains=search) |
                 users.filter(first_name__icontains=search) |
                 users.filter(email__icontains=search))
    if unlinked:
        users = users.filter(role='customer', distributor__isnull=True)

    deleted_count = User.all_objects.filter(is_deleted=True, is_purged=False).count()
    unlinked_count = User.objects.filter(role='customer', distributor__isnull=True, is_deleted=False).count()

    return render(request, 'admin_panel/user_list.html', {
        'users': users,
        'role_filter': role,
        'search': search,
        'show_deleted': show_deleted,
        'unlinked': unlinked,
        'deleted_count': deleted_count,
        'unlinked_count': unlinked_count,
        'role_choices': User.ROLE_CHOICES,
        'sort': sort,
        'distributors': Distributor.objects.filter(status='active').order_by('company_name'),
    })


@admin_required
def customer_list(request):
    """View specifically for customers only"""
    search = request.GET.get('q', '').strip()
    show_deleted = request.GET.get('deleted') == '1'
    unlinked = request.GET.get('unlinked') == '1'
    sort = request.GET.get('sort', '-date_joined')

    # Quick inline actions
    if request.method == 'POST':
        action = request.POST.get('action')
        user_id = request.POST.get('user_id')
        if user_id:
            target = get_object_or_404(User, pk=user_id)
            if action == 'delete':
                target.soft_delete(deleted_by=request.user.username)
                from lakrish_mart.activity_logger import act
                act.user('soft_deleted', user=target, by=request.user, detail='Customer')
                messages.success(request, f'Customer "{target.username}" deleted.')
            elif action == 'assign_distributor':
                dist_id = request.POST.get('distributor_id') or None
                target.distributor_id = dist_id
                target.save(update_fields=['distributor'])
                messages.success(request, f'Marketplace assigned to "{target.username}".')
        return redirect(request.get_full_path())

    SORT_MAP = {
        'username': 'username', '-username': '-username',
        'name': 'first_name', '-name': '-first_name',
        'date_joined': 'date_joined', '-date_joined': '-date_joined',
    }
    order_by = SORT_MAP.get(sort, '-date_joined')

    suspended_dist = request.GET.get('suspended_dist') == '1'

    # Chain filters: customer -> marketplace (distributor) -> wholesale
    # distributor (wholesaler) -> stockist. Filter the customer list by any tier.
    f_marketplace = request.GET.get('marketplace', '').strip()   # Distributor pk
    f_distributor = request.GET.get('distributor', '').strip()   # Wholesaler pk
    f_stockist = request.GET.get('stockist', '').strip()         # Stockist pk

    # Deleted customers are hidden by the default manager, so the deleted view
    # must query all_objects (otherwise ?deleted=1 is always empty).
    base_manager = User.all_objects if show_deleted else User.objects
    customers = base_manager.filter(role='customer').select_related(
        'distributor', 'distributor__wholesaler', 'distributor__wholesaler__stockist'
    ).order_by(order_by)

    if show_deleted:
        # Deleted tab = soft-deleted but not yet permanently archived (purged).
        customers = customers.filter(is_deleted=True, is_purged=False)
    else:
        customers = customers.filter(is_deleted=False)

    if search:
        customers = (customers.filter(username__icontains=search) |
                     customers.filter(first_name__icontains=search) |
                     customers.filter(email__icontains=search))
    if unlinked:
        customers = customers.filter(distributor__isnull=True)
    if suspended_dist:
        customers = customers.filter(distributor__status='suspended')
    # Chain filters (walk customer.distributor up the tiers).
    if f_marketplace:
        customers = customers.filter(distributor_id=f_marketplace)
    if f_distributor:
        customers = customers.filter(distributor__wholesaler_id=f_distributor)
    if f_stockist:
        customers = customers.filter(distributor__wholesaler__stockist_id=f_stockist)

    deleted_count = User.all_objects.filter(role='customer', is_deleted=True, is_purged=False).count()
    unlinked_count = User.objects.filter(role='customer', distributor__isnull=True, is_deleted=False).count()
    suspended_dist_count = User.objects.filter(role='customer', distributor__status='suspended', is_deleted=False).count()

    from stockist.models import Stockist
    return render(request, 'admin_panel/customer_list.html', {
        'customers': customers,
        'search': search,
        'show_deleted': show_deleted,
        'unlinked': unlinked,
        'suspended_dist': suspended_dist,
        'deleted_count': deleted_count,
        'unlinked_count': unlinked_count,
        'suspended_dist_count': suspended_dist_count,
        'sort': sort,
        'distributors': Distributor.objects.filter(status='active').order_by('company_name'),
        # Chain-filter option lists (ACTIVE only) + current selections:
        'marketplaces_f': Distributor.objects.filter(status='active', user__is_deleted=False).order_by('company_name'),
        'wholesalers_f': Wholesaler.objects.filter(status='active', user__is_deleted=False).order_by('company_name'),
        'stockists_f': Stockist.objects.filter(status='active', user__is_deleted=False).order_by('company_name'),
        'f_marketplace': f_marketplace,
        'f_distributor': f_distributor,
        'f_stockist': f_stockist,
    })


def _ensure_role_profile(user, role):
    """Create the business profile that a role needs, if it doesn't exist yet.

    Single-role policy: an account has one role, so only one profile is ever
    created here. Existing profiles from a previous role are left untouched
    (soft-history — we never hard-delete records).
    """
    if role == 'supplier':
        Supplier.objects.get_or_create(
            user=user, defaults={'company_name': f'{user.username} Supplier', 'status': 'approved'})
    elif role == 'distributor':
        Distributor.objects.get_or_create(
            user=user, defaults={'company_name': f'{user.username} Marketplace', 'status': 'active'})
    elif role == 'wholesaler':
        from distributors.wholesaler_models import Wholesaler
        Wholesaler.objects.get_or_create(
            user=user, defaults={'company_name': f'{user.username} Distributor', 'status': 'active'})


@admin_required
def user_create(request):
    role_choices = [
        (value, label)
        for value, label in User.ROLE_CHOICES
        if value not in ('admin', 'stockist')
    ]
    allowed_roles = {value for value, _ in role_choices}
    distributors_qs = Distributor.objects.filter(status='active').order_by('company_name')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()
        email = request.POST.get('email', '').strip()
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        phone = request.POST.get('phone', '').strip()
        
        # Single-role policy: exactly ONE role per account.
        selected_role = request.POST.get('role', '').strip()
        is_active = request.POST.get('is_active') == '1'
        distributor_id = request.POST.get('distributor_id') or None

        if not username or not password:
            messages.error(request, 'Username and password are required.')
            return render(request, 'admin_panel/user_create.html', {
                'role_choices': role_choices,
                'distributors': distributors_qs,
            })

        from accounts.utils import is_valid_username
        if not is_valid_username(username):
            messages.error(request, 'Username must be a valid mobile number or email ID.')
            return render(request, 'admin_panel/user_create.html', {
                'role_choices': role_choices,
                'distributors': distributors_qs,
            })

        if User.objects.filter(username=username).exists():
            messages.error(request, f'Username "{username}" already exists.')
            return render(request, 'admin_panel/user_create.html', {
                'role_choices': role_choices,
                'distributors': distributors_qs,
            })

        if selected_role not in allowed_roles:
            messages.error(request, 'Please choose exactly one role for this account.')
            return render(request, 'admin_panel/user_create.html', {
                'role_choices': role_choices,
                'distributors': distributors_qs,
            })

        user = User.objects.create_user(
            username=username,
            password=password,
            email=email,
            first_name=first_name,
            last_name=last_name,
            role=selected_role,
            phone=phone,
        )
        user.set_roles([selected_role])
        user.is_active = is_active
        if selected_role == 'customer' and distributor_id:
            user.distributor_id = distributor_id
        user.save()

        _ensure_role_profile(user, selected_role)

        messages.success(request, f'User "{username}" created successfully.')
        return redirect('admin_panel:user_list')

    return render(request, 'admin_panel/user_create.html', {
        'role_choices': role_choices,
        'distributors': distributors_qs,
    })


@admin_required
def user_detail(request, pk):
    # all_objects so a deleted account's detail page is still viewable.
    user = get_object_or_404(User.all_objects, pk=pk)
    supplier = getattr(user, 'supplier_profile', None)
    distributor = getattr(user, 'distributor_profile', None)
    orders = None
    if user.role == 'customer':
        orders = user.orders.order_by('-created_at')[:10]
    return render(request, 'admin_panel/user_detail.html', {
        'u': user,
        'supplier': supplier,
        'distributor': distributor,
        'orders': orders,
    })


@admin_required
def user_edit(request, pk):
    user = get_object_or_404(User, pk=pk)
    role_choices = [
        (value, label)
        for value, label in User.ROLE_CHOICES
        if value not in ('admin', 'stockist')
    ]
    allowed_roles = {value for value, _ in role_choices}

    if request.method == 'POST':
        user.first_name  = request.POST.get('first_name', '').strip()
        user.last_name   = request.POST.get('last_name', '').strip()
        user.email       = request.POST.get('email', '').strip()
        user.phone       = request.POST.get('phone', '').strip()
        user.is_active   = request.POST.get('is_active') == '1'

        # Single-role policy: exactly ONE role per account.
        selected_role = request.POST.get('role', '').strip()
        if selected_role not in allowed_roles:
            selected_role = user.primary_role()
        user.set_roles([selected_role])

        # Only a customer is linked to an Marketplace storefront.
        if selected_role == 'customer':
            user.distributor_id = request.POST.get('distributor_id') or None
        else:
            user.distributor_id = None

        user.save()
        _ensure_role_profile(user, selected_role)

        # Update linked profile if supplier
        supplier = getattr(user, 'supplier_profile', None)
        if supplier:
            supplier.company_name    = request.POST.get('company_name', supplier.company_name).strip()
            supplier.status          = request.POST.get('supplier_status', supplier.status)
            supplier.save()

        # Update linked profile if distributor
        distributor = getattr(user, 'distributor_profile', None)
        if distributor:
            distributor.company_name           = request.POST.get('company_name', distributor.company_name).strip()
            distributor.area                   = request.POST.get('area', distributor.area).strip()
            distributor.delivery_charge        = request.POST.get('delivery_charge', distributor.delivery_charge)
            distributor.free_delivery_above    = request.POST.get('free_delivery_above', distributor.free_delivery_above)
            distributor.default_margin_percent = request.POST.get('default_margin_percent', distributor.default_margin_percent)
            distributor.status                 = request.POST.get('dist_status', distributor.status)
            distributor.save()

        messages.success(request, f'User "{user.username}" updated.')
        return redirect('admin_panel:user_detail', pk=pk)

    return render(request, 'admin_panel/user_edit.html', {
        'u': user,
        'supplier': getattr(user, 'supplier_profile', None),
        'distributor': getattr(user, 'distributor_profile', None),
        'role_choices': role_choices,
        'current_roles': user.get_roles_list(),
        'distributors': Distributor.objects.filter(status='active').order_by('company_name'),
    })


@admin_required
def user_reset_password(request, pk):
    user = get_object_or_404(User, pk=pk)
    if request.method == 'POST':
        new_password = request.POST.get('new_password', '').strip()
        confirm      = request.POST.get('confirm_password', '').strip()
        if not new_password:
            messages.error(request, 'Password cannot be empty.')
        elif new_password != confirm:
            messages.error(request, 'Passwords do not match.')
        elif len(new_password) < 6:
            messages.error(request, 'Password must be at least 6 characters.')
        else:
            user.set_password(new_password)
            user.save()
            messages.success(request, f'Password for "{user.username}" has been reset.')
            return redirect('admin_panel:user_detail', pk=pk)
    return render(request, 'admin_panel/user_reset_password.html', {'u': user})


@admin_required
@require_POST
def user_toggle_active(request, pk):
    user = get_object_or_404(User, pk=pk)
    if user.username == 'admin':
        messages.error(request, 'Cannot deactivate the main admin account.')
        return redirect('admin_panel:user_list')
    user.is_active = not user.is_active
    user.save()
    state = 'activated' if user.is_active else 'deactivated'
    messages.success(request, f'"{user.username}" {state}.')
    return redirect('admin_panel:user_detail', pk=pk)


@admin_required
def user_delete(request, pk):
    user = get_object_or_404(User, pk=pk)
    if user.username == 'admin' or user.is_superuser:
        messages.error(request, 'Cannot delete the admin account.')
        return redirect('admin_panel:user_list')

    # All accounts follow the no-hard-delete policy. Soft deletion preserves
    # profiles, products, orders, and audit history for later restoration.
    if request.method == 'POST':
        user.soft_delete(deleted_by=request.user.username)
        messages.success(request, f'User "{user.username}" moved to deleted accounts.')
        return redirect('admin_panel:user_list')

    return render(request, 'admin_panel/user_confirm_delete.html', {'u': user})


@admin_required
@require_POST
def user_restore(request, pk):
    # Deleted users live only in all_objects (default manager hides them).
    user = get_object_or_404(User.all_objects, pk=pk, is_deleted=True)
    user.restore()
    from lakrish_mart.activity_logger import act
    act.user('restored', user=user, by=request.user)
    messages.success(request, f'User "{user.username}" restored.')
    return redirect(request.META.get('HTTP_REFERER', 'admin_panel:user_list'))


@admin_required
@require_POST
def user_purge(request, pk):
    """Permanently archive (hard-delete) a soft-deleted user/customer.
    Hard delete = permanent archive (never a physical row delete) — see
    kb/09-account-lifecycle.md."""
    user = get_object_or_404(User.all_objects, pk=pk, is_deleted=True)
    if user.username == 'admin' or user.is_superuser:
        messages.error(request, 'This account cannot be permanently deleted.')
        return redirect(request.META.get('HTTP_REFERER', 'admin_panel:user_list'))
    name = user.username
    user.purge()
    from lakrish_mart.activity_logger import act
    act.user('hard_deleted', user=user, by=request.user, detail='permanent archive')
    messages.warning(request, f'"{name}" permanently archived. This cannot be undone.')
    return redirect(request.META.get('HTTP_REFERER', 'admin_panel:user_list'))


@admin_required
def bulk_user_action(request):
    """Bulk restore for deleted accounts (no permanent deletion)."""
    if request.method == 'POST':
        action = request.POST.get('action')
        ids = request.POST.getlist('user_ids')
        if ids and action == 'restore':
            qs = User.all_objects.filter(pk__in=ids, is_deleted=True).exclude(is_superuser=True)
            count = 0
            for u in qs:
                u.restore()
                count += 1
            messages.success(request, f'{count} user(s) restored.')
        else:
            messages.warning(request, 'No users selected or invalid action.')
    from django.urls import reverse
    return redirect(reverse('admin_panel:user_list') + '?deleted=1')


# ── Price History ─────────────────────────────────────────────────────────────

@admin_required
def price_history(request):
    from products.models import VariantPriceHistory
    supplier_id = request.GET.get('supplier')
    product_id  = request.GET.get('product')
    variant_id  = request.GET.get('variant')

    history = VariantPriceHistory.objects.select_related(
        'variant__product__supplier'
    ).order_by('-changed_at')

    if supplier_id:
        history = history.filter(variant__product__supplier_id=supplier_id)
    if product_id:
        history = history.filter(variant__product_id=product_id)
    if variant_id:
        history = history.filter(variant_id=variant_id)

    suppliers = Supplier.objects.filter(status='approved')
    products  = Product.objects.filter(status='approved').select_related('supplier')

    return render(request, 'admin_panel/price_history.html', {
        'history': history[:200],
        'suppliers': suppliers,
        'products': products,
        'sel_supplier': supplier_id,
        'sel_product': product_id,
    })


# ── Password Reset Requests ───────────────────────────────────────────────────

@admin_required
def reset_requests(request):
    from accounts.models import PasswordResetRequest
    requests_qs = PasswordResetRequest.objects.select_related('user', 'resolved_by').all()
    return render(request, 'admin_panel/reset_requests.html', {'reset_requests': requests_qs})


@admin_required
def reset_request_action(request, pk):
    from accounts.models import PasswordResetRequest
    reset_req = get_object_or_404(PasswordResetRequest, pk=pk)
    if request.method == 'POST':
        new_password = request.POST.get('new_password', '').strip()
        if not new_password or len(new_password) < 6:
            messages.error(request, 'Password must be at least 6 characters.')
            return redirect('admin_panel:reset_requests')
        reset_req.user.set_password(new_password)
        reset_req.user.save()
        reset_req.status = 'resolved'
        reset_req.resolved_by = request.user
        reset_req.resolved_at = timezone.now()
        reset_req.save()
        messages.success(request, f'Password reset for {reset_req.user.username}.')
    return redirect('admin_panel:reset_requests')


# ── Export Orders ─────────────────────────────────────────────────────────────

@admin_required
def export_orders(request):
    import openpyxl
    from django.http import HttpResponse
    fmt = request.GET.get('fmt', 'xlsx')
    orders = Order.objects.select_related('customer', 'distributor').prefetch_related('items').order_by('-created_at')

    if fmt == 'csv':
        import csv
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="orders.csv"'
        writer = csv.writer(response)
        writer.writerow(['Order ID', 'Customer', 'Distributor', 'Total', 'Status', 'Date'])
        for o in orders:
            writer.writerow([o.id, o.customer.username, o.distributor.company_name if o.distributor else '', o.total_amount, o.status, o.created_at.strftime('%d/%m/%Y')])
        return response

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Orders'
    ws.append(['Order ID', 'Customer', 'Phone', 'Distributor', 'Subtotal', 'Delivery', 'Total', 'Status', 'Date'])
    for o in orders:
        ws.append([o.id, o.customer.get_full_name() or o.customer.username, o.phone,
                   o.distributor.company_name if o.distributor else '',
                   float(o.subtotal), float(o.delivery_charge), float(o.total_amount),
                   o.status, o.created_at.strftime('%d/%m/%Y')])
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="orders.xlsx"'
    wb.save(response)
    return response


# ── Low Stock Alerts ──────────────────────────────────────────────────────────

@admin_required
def low_stock(request):
    threshold = int(request.GET.get('threshold', 10))
    from products.models import ProductVariant
    variants = ProductVariant.objects.shared().filter(
        stock__lte=threshold, is_active=True, product__is_deleted=False
    ).select_related('product__supplier').order_by('stock')
    return render(request, 'admin_panel/low_stock.html', {'variants': variants, 'threshold': threshold})


# ── Bulk Product Actions ──────────────────────────────────────────────────────

@admin_required
def bulk_product_action(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        ids = request.POST.getlist('product_ids')
        if ids:
            from products.models import Product
            if action in ('approve', 'reject'):
                status = 'approved' if action == 'approve' else 'rejected'
                count = Product.objects.filter(pk__in=ids, is_deleted=False).update(status=status)
                messages.success(request, f'{count} product(s) {status}.')
            elif action == 'delete':
                from django.utils import timezone
                count = Product.objects.filter(pk__in=ids, is_deleted=False).update(
                    is_deleted=True, deleted_at=timezone.now(), status='rejected'
                )
                messages.success(request, f'{count} product(s) deleted.')
            elif action == 'restore':
                count = Product.objects.filter(pk__in=ids, is_deleted=True).update(
                    is_deleted=False, deleted_at=None, status='approved'
                )
                messages.success(request, f'{count} product(s) restored.')
    return redirect(request.META.get('HTTP_REFERER', 'admin_panel:product_list'))


@admin_required
def bulk_supplier_action(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        ids = request.POST.getlist('supplier_ids')
        if ids and action in ('suspend', 'activate'):
            new_status = 'suspended' if action == 'suspend' else 'approved'
            count = Supplier.objects.filter(pk__in=ids).update(status=new_status)
            from lakrish_mart.activity_logger import act
            act.info(f'BULK supplier {action} | {count} account(s) -> {new_status} | by={request.user.get_username()}')
            messages.success(request, f'{count} supplier(s) {new_status}.')
        elif ids:
            _bulk_lifecycle(request, Supplier, ids, action, 'supplier',
                            request.user.get_username())
    return redirect(request.META.get('HTTP_REFERER', 'admin_panel:supplier_list'))


@admin_required
def bulk_distributor_action(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        ids = request.POST.getlist('distributor_ids')
        if ids and action in ('suspend', 'activate'):
            new_status = 'suspended' if action == 'suspend' else 'active'
            count = Distributor.objects.filter(pk__in=ids).update(status=new_status)
            from lakrish_mart.activity_logger import act
            act.info(f'BULK marketplace {action} | {count} account(s) -> {new_status} | by={request.user.get_username()}')
            messages.success(request, f'{count} marketplace(s) {new_status}.')
        elif ids:
            _bulk_lifecycle(request, Distributor, ids, action, 'marketplace',
                            request.user.get_username())
    return redirect(request.META.get('HTTP_REFERER', 'admin_panel:distributor_list'))


@admin_required
def bulk_stockist_action(request):
    from stockist.models import Stockist

    if request.method == 'POST':
        action = request.POST.get('action')
        ids = request.POST.getlist('stockist_ids')
        if ids and action in ('suspend', 'activate'):
            new_status = 'suspended' if action == 'suspend' else 'active'
            count = Stockist.objects.filter(pk__in=ids).update(status=new_status)
            from lakrish_mart.activity_logger import act
            act.info(f'BULK stockist {action} | {count} account(s) -> {new_status} | by={request.user.get_username()}')
            messages.success(request, f'{count} stockist(s) {new_status}.')
        elif ids:
            _bulk_lifecycle(request, Stockist, ids, action, 'stockist',
                            request.user.get_username())
    return redirect(request.META.get('HTTP_REFERER', 'admin_panel:stockist_list'))


@admin_required
def bulk_wholesaler_action(request):
    """Bulk suspend/activate wholesale Distributors (code: Wholesaler)."""
    if request.method == 'POST':
        action = request.POST.get('action')
        ids = request.POST.getlist('wholesaler_ids')
        if ids and action in ('suspend', 'activate'):
            new_status = 'suspended' if action == 'suspend' else 'active'
            count = Wholesaler.objects.filter(pk__in=ids).update(status=new_status)
            from lakrish_mart.activity_logger import act
            act.info(f'BULK distributor {action} | {count} account(s) -> {new_status} | by={request.user.get_username()}')
            messages.success(request, f'{count} distributor(s) {new_status}.')
        elif ids:
            _bulk_lifecycle(request, Wholesaler, ids, action, 'distributor',
                            request.user.get_username())
    return redirect(request.META.get('HTTP_REFERER', 'admin_panel:wholesaler_list'))


# ── Account lifecycle: soft-delete / restore / purge for partner roles ────────
# See kb/09-account-lifecycle.md. These operate on the partner's linked User, so
# all four roles share one mechanism. The `role` kwarg picks the model + list.

_PARTNER_LIFECYCLE = {
    # role_key: (import path getter, list url name, label)
    'supplier':   ('admin_panel:supplier_list',   'Supplier'),
    'stockist':   ('admin_panel:stockist_list',   'Stockist'),
    'wholesaler': ('admin_panel:wholesaler_list', 'Distributor'),
    'distributor':('admin_panel:distributor_list','Marketplace'),
}


def _partner_model(role):
    from stockist.models import Stockist
    if role == 'supplier':
        return Supplier
    if role == 'stockist':
        return Stockist
    if role == 'wholesaler':
        return Wholesaler
    if role == 'distributor':
        return Distributor
    return None


def _bulk_lifecycle(request, model, ids, action, label, deleted_by):
    """Handle the delete/restore/purge bulk branch for any partner role.
    Operates on the linked User. Returns True if it handled the action."""
    if action not in ('delete', 'restore', 'purge'):
        return False
    from lakrish_mart.activity_logger import act
    objs = model.objects.filter(pk__in=ids).select_related('user')
    count = 0
    _event = {'delete': 'soft_deleted', 'restore': 'restored', 'purge': 'hard_deleted'}[action]
    for obj in objs:
        acted = False
        if action == 'delete':
            if not obj.user.is_deleted:
                obj.user.soft_delete(deleted_by=deleted_by)
                acted = True
        elif action == 'restore':
            if obj.user.is_deleted and not obj.user.is_purged:
                obj.user.restore()
                acted = True
        elif action == 'purge':
            if not obj.user.is_purged:
                obj.user.purge()
                acted = True
        if acted:
            count += 1
            act.user(_event, user=obj.user, by=request.user,
                     detail=f'{label}: {obj.company_name} (bulk)')
    verb = {'delete': 'moved to Deleted', 'restore': 'restored', 'purge': 'permanently archived'}[action]
    messages.success(request, f'{count} {label}(s) {verb}.')
    return True


@admin_required
@require_POST
def partner_soft_delete(request, role, pk):
    """Soft-delete a partner (from the Suspended list) → moves to Deleted tab."""
    model = _partner_model(role)
    list_url, label = _PARTNER_LIFECYCLE.get(role, ('admin_panel:dashboard', 'Account'))
    if model is None:
        messages.error(request, 'Unknown role.')
        return redirect('admin_panel:dashboard')
    obj = get_object_or_404(model, pk=pk)
    obj.user.soft_delete(deleted_by=request.user.get_username())
    from lakrish_mart.activity_logger import act
    act.user('soft_deleted', user=obj.user, by=request.user, detail=f'{label}: {obj.company_name}')
    messages.warning(request, f'{label} "{obj.company_name}" moved to Deleted. Recoverable until the grace period ends.')
    return redirect(request.META.get('HTTP_REFERER', list_url))


@admin_required
@require_POST
def partner_restore(request, role, pk):
    """Restore a soft-deleted partner (from the Deleted list)."""
    model = _partner_model(role)
    list_url, label = _PARTNER_LIFECYCLE.get(role, ('admin_panel:dashboard', 'Account'))
    if model is None:
        messages.error(request, 'Unknown role.')
        return redirect('admin_panel:dashboard')
    # Use all_objects via the profile's user; the profile row itself is intact.
    obj = get_object_or_404(model.objects.all(), pk=pk) if hasattr(model, 'objects') else None
    if obj is None:
        obj = get_object_or_404(model, pk=pk)
    if obj.user.is_purged:
        messages.error(request, f'{label} "{obj.company_name}" is permanently archived and cannot be restored.')
    else:
        obj.user.restore()
        from lakrish_mart.activity_logger import act
        act.user('restored', user=obj.user, by=request.user, detail=f'{label}: {obj.company_name}')
        messages.success(request, f'{label} "{obj.company_name}" restored.')
    return redirect(request.META.get('HTTP_REFERER', list_url))


@admin_required
@require_POST
def partner_purge(request, role, pk):
    """Permanently archive (hard-delete) a soft-deleted partner immediately."""
    model = _partner_model(role)
    list_url, label = _PARTNER_LIFECYCLE.get(role, ('admin_panel:dashboard', 'Account'))
    if model is None:
        messages.error(request, 'Unknown role.')
        return redirect('admin_panel:dashboard')
    obj = get_object_or_404(model, pk=pk)
    obj.user.purge()
    from lakrish_mart.activity_logger import act
    act.user('hard_deleted', user=obj.user, by=request.user, detail=f'{label}: {obj.company_name} (permanent archive)')
    messages.warning(request, f'{label} "{obj.company_name}" permanently archived. This cannot be undone.')
    return redirect(request.META.get('HTTP_REFERER', list_url))

@admin_required
def stockist_list(request):
    from stockist.models import Stockist
    status_filter = request.GET.get('status', 'active')
    search = request.GET.get('q', '').strip()
    if status_filter == 'deleted':
        from admin_panel.purge import purge_due_accounts
        purge_due_accounts()
        stockists = Stockist.objects.select_related('user').filter(
            user__is_deleted=True, user__is_purged=False)
    else:
        stockists = Stockist.objects.select_related('user').filter(user__is_deleted=False)
        if status_filter and status_filter != 'all':
            stockists = stockists.filter(status=status_filter)
    if search:
        stockists = (stockists.filter(company_name__icontains=search) |
                    stockists.filter(user__username__icontains=search))
    active_count = Stockist.objects.filter(status='active', user__is_deleted=False).count()
    suspended_count = Stockist.objects.filter(status='suspended', user__is_deleted=False).count()
    deleted_count = Stockist.objects.filter(user__is_deleted=True, user__is_purged=False).count()
    from django.urls import reverse
    return render(request, 'admin_panel/stockist_list.html', {
        'stockists': stockists,
        'status_filter': status_filter,
        'active_count': active_count,
        'suspended_count': suspended_count,
        'deleted_count': deleted_count,
        'search': search,
        # Common partner-list layout config:
        'page_title': 'Manage Stockists',
        'active_nav': 'stockist_list',
        'show_tabs': True,
        'tab_active_value': 'active',
        'show_search': True,
        'table_id': 'stockistTable',
        'count': stockists.count(),
        'count_noun': 'stockist',
        'create_url': reverse('admin_panel:stockist_create'),
        'create_label': 'Create Stockist',
        'bulk_action_url': reverse('admin_panel:bulk_stockist_action'),
        'empty_text': 'No stockists found.',
    })


@admin_required
@require_POST
def stockist_toggle(request, pk):
    from stockist.models import Stockist
    stockist = get_object_or_404(Stockist, pk=pk)
    stockist.status = 'suspended' if stockist.status == 'active' else 'active'
    stockist.save()
    from lakrish_mart.activity_logger import act
    act.user('suspended' if stockist.status == 'suspended' else 'activated',
             user=stockist.user, by=request.user, detail=f'Stockist: {stockist.company_name}')
    messages.success(request, f'{stockist.company_name} is now {stockist.status}.')
    return redirect('admin_panel:stockist_list')


@admin_required
def stockist_create(request):
    """Admin creates the stockist account."""
    from stockist.models import Stockist
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()
        email    = request.POST.get('email', '').strip()
        name     = request.POST.get('first_name', '').strip()
        phone    = request.POST.get('phone', '').strip()
        company  = request.POST.get('company_name', '').strip()
        area     = request.POST.get('area', '').strip()

        if not username or not password or not company:
            messages.error(request, 'Username, password and company name are required.')
            return render(request, 'admin_panel/stockist_create.html', {'post': request.POST})

        from accounts.utils import is_valid_username
        if not is_valid_username(username):
            messages.error(request, 'Username must be a valid mobile number or email ID.')
            return render(request, 'admin_panel/stockist_create.html', {'post': request.POST})

        if User.objects.filter(username=username).exists():
            messages.error(request, f'Username "{username}" already exists.')
            return render(request, 'admin_panel/stockist_create.html', {'post': request.POST})

        user = User.objects.create_user(
            username=username, password=password, email=email,
            first_name=name, role='stockist', roles='stockist', phone=phone,
        )
        Stockist.objects.create(user=user, company_name=company, area=area, status='active')
        messages.success(request, f'Stockist "{company}" created.')
        return redirect('admin_panel:stockist_list')

    return render(request, 'admin_panel/stockist_create.html', {})


@admin_required
def stockist_restock_list(request):
    """Admin sees all stockist restock requests and can fulfill them."""
    from stockist.models import StockistRestockRequest, StockistInventory, Stockist
    requests_qs = StockistRestockRequest.objects.select_related(
        'stockist', 'variant__product__supplier'
    ).all()

    if request.method == 'POST':
        req_id = request.POST.get('req_id')
        action = request.POST.get('action')
        try:
            req = StockistRestockRequest.objects.get(pk=req_id)
            if action == 'fulfill':
                # Add to stockist inventory
                inv, _ = StockistInventory.objects.get_or_create(
                    stockist=req.stockist, variant=req.variant, defaults={'quantity': 0}
                )
                inv.quantity += req.quantity_requested
                inv.save()
                req.status = 'fulfilled'
                from django.utils import timezone
                req.fulfilled_at = timezone.now()
                req.save()
                messages.success(request, f'Fulfilled {req.quantity_requested} units of {req.variant}.')
            elif action == 'reject':
                req.status = 'rejected'
                req.save()
                messages.warning(request, 'Request rejected.')
        except StockistRestockRequest.DoesNotExist:
            messages.error(request, 'Request not found.')
        return redirect('admin_panel:stockist_restock')

    return render(request, 'admin_panel/stockist_restock.html', {
        'requests': requests_qs,
        'pending_count': requests_qs.filter(status='pending').count(),
    })


@admin_required
def impersonate(request, pk):
    """Admin logs in as another user to view the site from their perspective."""
    import logging
    _security_log = logging.getLogger('security')

    target = get_object_or_404(User, pk=pk)
    if target.is_superuser:
        messages.error(request, 'Cannot impersonate a superuser.')
        return redirect('admin_panel:user_list')

    # Store admin's real user id BEFORE login() which may flush session
    impersonator_id = request.user.pk
    impersonator_name = request.user.get_full_name() or request.user.username

    _security_log.warning(
        'IMPERSONATE START: admin=%s (id=%s) -> target=%s (id=%s) from ip=%s',
        impersonator_name, impersonator_id,
        target.username, target.pk,
        request.META.get('REMOTE_ADDR', 'unknown'),
    )

    # Log in as target user using the project's custom backend
    from django.contrib.auth import login
    target.backend = 'accounts.backends.CaseInsensitiveBackend'
    login(request, target)

    # Restore impersonation markers AFTER login (login may cycle the session)
    request.session['impersonator_id'] = impersonator_id
    request.session['impersonator_name'] = impersonator_name
    request.session['impersonation_started_at'] = timezone.now().isoformat()

    # Set active role to their primary role
    request.session['active_role'] = target.role

    messages.info(request, f'You are now viewing as {target.username}. Click "Return to Admin" to go back.')

    # Redirect to their natural home
    role = target.role
    if role == 'distributor':
        return redirect('distributors:dashboard')
    elif role == 'supplier':
        return redirect('suppliers:dashboard')
    elif role == 'customer':
        return redirect('products:home')
    return redirect('products:home')


def impersonate_return(request):
    """Return to admin account after impersonating."""
    import logging
    _security_log = logging.getLogger('security')

    admin_id = request.session.get('impersonator_id')
    if not admin_id:
        return redirect('accounts:login')

    admin_user = get_object_or_404(User, pk=admin_id)

    # Verify the stored user is actually an admin to prevent privilege escalation
    if not admin_user.is_superuser and 'admin' not in admin_user.get_roles_list():
        request.session.pop('impersonator_id', None)
        request.session.pop('impersonator_name', None)
        messages.error(request, 'Invalid impersonation session.')
        return redirect('accounts:login')

    _security_log.warning(
        'IMPERSONATE END: admin=%s (id=%s) returning from target=%s from ip=%s started_at=%s',
        admin_user.username, admin_user.pk,
        request.user.username,
        request.META.get('REMOTE_ADDR', 'unknown'),
        request.session.get('impersonation_started_at', 'unknown'),
    )

    from django.contrib.auth import login
    admin_user.backend = 'accounts.backends.CaseInsensitiveBackend'
    login(request, admin_user)
    request.session['active_role'] = 'admin'
    request.session.pop('impersonator_id', None)
    request.session.pop('impersonator_name', None)
    request.session.pop('impersonation_started_at', None)
    messages.success(request, 'Returned to admin account.')
    return redirect('admin_panel:dashboard')


def supplier_shipment_api(request):
    """Live DB data for the supplier-shipment-routing interactive explainer.

    Read-only JSON. Actions:
      ?action=summary   → counts per delivery_target + per shipment status
      ?action=products  → each product's routing (target, mode) + skip map
      ?action=shipments → recent SupplierShipment rows (live order lines)
    """
    from orders.models import SupplierShipment

    # The chain order used to compute which tiers a target skips.
    CHAIN = ['supplier', 'stockist', 'distributor', 'shop', 'customer']

    def skipped_tiers(target):
        """Tiers strictly between supplier and the target (exclusive) = skipped."""
        try:
            idx = CHAIN.index(target)
        except ValueError:
            idx = 1  # default stockist
        # everything after supplier(0) and before the target is skipped
        return CHAIN[1:idx]

    action = request.GET.get('action', 'summary').strip()

    if action == 'summary':
        target_counts = {}
        for key, label in Product.DELIVERY_TARGET_CHOICES:
            target_counts[key] = {
                'label': label,
                'count': Product.objects.filter(
                    delivery_target=key, is_deleted=False
                ).count(),
            }
        mode_counts = {}
        for key, label in Product.SHIPMENT_MODE_CHOICES:
            mode_counts[key] = {
                'label': label,
                'count': Product.objects.filter(
                    shipment_mode=key, is_deleted=False
                ).count(),
            }
        status_counts = {}
        for key, label in SupplierShipment.STATUS_CHOICES:
            status_counts[key] = {
                'label': label,
                'count': SupplierShipment.objects.filter(status=key).count(),
            }
        return JsonResponse({
            'ok': True,
            'chain': CHAIN,
            'targets': target_counts,
            'modes': mode_counts,
            'shipment_status': status_counts,
            'total_products': Product.objects.filter(is_deleted=False).count(),
            'total_shipments': SupplierShipment.objects.count(),
        })

    if action == 'products':
        rows = []
        qs = (Product.objects.filter(is_deleted=False)
              .select_related('supplier', 'category')
              .order_by('supplier__company_name', 'name'))
        for p in qs[:500]:
            rows.append({
                'id': p.pk,
                'name': p.name,
                'sku': p.sku,
                'supplier': p.supplier.company_name if p.supplier else '–',
                'category': p.category.name if p.category else '–',
                'status': p.status,
                'target': p.delivery_target,
                'target_label': p.get_delivery_target_display(),
                'mode': p.shipment_mode,
                'mode_label': p.get_shipment_mode_display(),
                'skipped': skipped_tiers(p.delivery_target),
            })
        return JsonResponse({'ok': True, 'products': rows, 'chain': CHAIN})

    if action == 'shipments':
        rows = []
        qs = (SupplierShipment.objects
              .select_related('order_item__order', 'order_item__product', 'supplier')
              .order_by('-created_at')[:100])
        for s in qs:
            oi = s.order_item
            rows.append({
                'id': s.pk,
                'order_id': oi.order_id,
                'product': oi.product.name if oi.product else '–',
                'variant': oi.variant_label or (oi.variant.label if oi.variant else '–'),
                'qty': oi.quantity,
                'supplier': s.supplier.company_name if s.supplier else '–',
                'target': s.target,
                'target_label': s.get_target_display(),
                'mode': s.mode,
                'mode_label': s.get_mode_display(),
                'status': s.status,
                'status_label': s.get_status_display(),
                'carrier': s.carrier,
                'tracking_reference': s.tracking_reference,
                'skipped': skipped_tiers(s.target),
                'created_at': s.created_at.strftime('%Y-%m-%d %H:%M'),
            })
        return JsonResponse({'ok': True, 'shipments': rows, 'chain': CHAIN})

    return JsonResponse({'ok': False, 'error': 'Unsupported action'}, status=400)


# ── Partner Hierarchy (read-only org tree) ────────────────────────────────────

@admin_required
def hierarchy(request):
    """Tree of the whole partner org, top-down from the Stockist, with admin
    reassignment controls.

    Chain (code name -> UI label):
        Stockist -> Wholesaler ("Distributor") -> Distributor ("Marketplace/Shop") -> Customer

    One Stockist serves many wholesale Distributors; each wholesale Distributor
    buys from exactly ONE Stockist and supplies many shops; each shop has many
    customers. Suppliers are not hard-linked to a single stockist, so they are
    listed separately.

    POST actions (admin interchange controls):
      * move_wholesaler  — reassign ONE distributor to a different stockist.
      * move_all         — move EVERY distributor under one stockist to another.
      * move_shop        — move ONE shop to a different wholesale distributor.

    Reassigning the stockist changes which processing charges apply and where
    future restock requests go; moving a shop changes which wholesale margin
    applies. Existing stock and orders are not touched.
    """
    from stockist.models import Stockist

    # Default behaviour: HIDE suspended accounts. Add ?show_suspended=1 to show
    # them (the template toggle does this).
    show_suspended = request.GET.get('show_suspended') == '1'

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'move_wholesaler':
            w = get_object_or_404(Wholesaler, pk=request.POST.get('wholesaler_pk'))
            raw = request.POST.get('stockist_pk', '')
            from lakrish_mart.activity_logger import act
            if raw:
                target = get_object_or_404(Stockist, pk=raw, status='active')
                w.stockist = target
                w.save(update_fields=['stockist'])
                act.info(f'HIERARCHY | distributor "{w.company_name}" moved to stockist "{target.company_name}" | by={request.user.get_username()}')
                messages.success(request, f'"{w.company_name}" now buys from {target.company_name}.')
            else:
                w.stockist = None
                w.save(update_fields=['stockist'])
                act.info(f'HIERARCHY | distributor "{w.company_name}" unassigned from stockist | by={request.user.get_username()}')
                messages.success(request, f'"{w.company_name}" now has no assigned stockist (uses the default).')
            return redirect('admin_panel:hierarchy')

        if action == 'move_all':
            source = get_object_or_404(Stockist, pk=request.POST.get('from_stockist_pk'))
            target = get_object_or_404(Stockist, pk=request.POST.get('to_stockist_pk'), status='active')
            if source.pk == target.pk:
                messages.error(request, 'Source and target stockist are the same — nothing to move.')
            else:
                count = Wholesaler.objects.filter(stockist=source).update(stockist=target)
                from lakrish_mart.activity_logger import act
                act.info(f'HIERARCHY | moved {count} distributor(s) from stockist "{source.company_name}" to "{target.company_name}" | by={request.user.get_username()}')
                messages.success(
                    request,
                    f'Moved {count} distributor(s) from {source.company_name} to {target.company_name}.'
                )
            return redirect('admin_panel:hierarchy')

        if action == 'move_shop':
            shop = get_object_or_404(Distributor, pk=request.POST.get('shop_pk'))
            raw = request.POST.get('wholesaler_pk', '')
            if raw:
                target = get_object_or_404(Wholesaler, pk=raw)
                shop.wholesaler = target
                shop.save(update_fields=['wholesaler'])
                from lakrish_mart.activity_logger import act
                act.info(f'HIERARCHY | shop "{shop.company_name}" moved to distributor "{target.company_name}" | by={request.user.get_username()}')
                messages.success(request, f'Moved "{shop.company_name}" to distributor {target.company_name}.')
            else:
                shop.wholesaler = None
                shop.save(update_fields=['wholesaler'])
                from lakrish_mart.activity_logger import act
                act.info(f'HIERARCHY | shop "{shop.company_name}" detached from distributor | by={request.user.get_username()}')
                messages.success(request, f'Detached "{shop.company_name}" from its distributor.')
            return redirect('admin_panel:hierarchy')

    # Shops (Distributor) annotated with their customer + order counts.
    shops = (Distributor.objects.select_related('user', 'wholesaler')
             .filter(user__is_deleted=False)
             .annotate(
                 customer_count=Count('customers', filter=Q(customers__is_deleted=False), distinct=True),
                 order_count=Count('orders', distinct=True),
             ))
    if not show_suspended:
        shops = shops.exclude(status='suspended')

    # Group shops by their wholesale distributor id (None = unassigned).
    shops_by_wholesaler = {}
    for shop in shops:
        shops_by_wholesaler.setdefault(shop.wholesaler_id, []).append(shop)

    # Wholesale Distributors grouped by their assigned stockist id (None = unassigned).
    wholesalers = (Wholesaler.objects.select_related('user', 'stockist')
                   .order_by('company_name'))
    if not show_suspended:
        wholesalers = wholesalers.exclude(status='suspended')
    wholesalers_by_stockist = {}
    for w in wholesalers:
        wholesalers_by_stockist.setdefault(w.stockist_id, []).append(w)

    def _build_wholesaler_node(w):
        w_shops = shops_by_wholesaler.get(w.pk, [])
        return {
            'obj': w,
            'shops': w_shops,
            'shop_count': len(w_shops),
            'customer_total': sum(s.customer_count for s in w_shops),
        }

    # Root = each Stockist, with its wholesale Distributors and their shops.
    stockists = Stockist.objects.select_related('user').order_by('company_name')
    if not show_suspended:
        stockists = stockists.exclude(status='suspended')
    tree = []
    for s in stockists:
        w_nodes = [_build_wholesaler_node(w) for w in wholesalers_by_stockist.get(s.pk, [])]
        tree.append({
            'obj': s,
            'wholesalers': w_nodes,
            'wholesaler_count': len(w_nodes),
            'shop_count': sum(n['shop_count'] for n in w_nodes),
            'customer_total': sum(n['customer_total'] for n in w_nodes),
        })

    # Wholesale Distributors with no stockist assigned (fall back to default).
    # When suspended stockists are hidden, also surface any (active) distributor
    # whose stockist is hidden, so it stays visible and movable instead of
    # vanishing with its suspended root.
    shown_stockist_ids = {s.pk for s in stockists}
    orphan_wholesalers = []
    for stockist_id, ws in wholesalers_by_stockist.items():
        if stockist_id is not None and stockist_id not in shown_stockist_ids:
            orphan_wholesalers.extend(ws)
    unassigned_wholesalers = [_build_wholesaler_node(w)
                              for w in wholesalers_by_stockist.get(None, []) + orphan_wholesalers]
    # Shops with no wholesale distributor assigned.
    unassigned_shops = shops_by_wholesaler.get(None, [])

    suppliers = Supplier.objects.select_related('user').order_by('company_name')
    if not show_suspended:
        suppliers = suppliers.exclude(status='suspended')

    # Option lists for the interchange dropdowns.
    active_stockists = Stockist.objects.filter(status='active').order_by('company_name')
    all_wholesalers = list(wholesalers)  # already ordered by company_name

    return render(request, 'admin_panel/hierarchy.html', {
        'tree': tree,
        'unassigned_wholesalers': unassigned_wholesalers,
        'unassigned_shops': unassigned_shops,
        'suppliers': suppliers,
        'supplier_count': suppliers.count(),
        'stockist_count': len(tree),
        'wholesaler_count': wholesalers.count(),
        'shop_count': shops.count(),
        'active_stockists': active_stockists,
        'all_wholesalers': all_wholesalers,
        'show_suspended': show_suspended,
    })
