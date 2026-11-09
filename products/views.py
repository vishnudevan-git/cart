import json
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.db.models import Avg, Count
from .models import Product, Category, ProductVariant


def get_distributor(request):
    """Returns the active Distributor for this request."""
    from distributors.models import Distributor
    if request.user.is_authenticated and getattr(request.user, 'distributor', None):
        if getattr(request.user.distributor, 'status', None) == 'active':
            return request.user.distributor
    dist_id = request.session.get('distributor_id')
    if dist_id:
        try:
            return Distributor.objects.get(pk=dist_id, status='active')
        except Distributor.DoesNotExist:
            pass
    return None


def _product_qs(distributor):
    from django.db.models import Q
    if distributor:
        listed_ids = distributor.dist_products.filter(is_listed=True).values_list('product_id', flat=True)
        # Show approved products + pending products that are self-listed (charges_confirmed=True)
        # Never show deleted products
        # all_objects: this shop's OWN products are private to it, so they are
        # hidden from the default (shared) manager — but its own customers must
        # see them. Everything here is already scoped to this shop's listings.
        # Direct-supply products never get stockist charges — approved is enough.
        qs = Product.all_objects.visible_to_enterprise(distributor).filter(
            Q(status='approved', charges_confirmed=True) |
            Q(status='approved', direct_supply=True) |
            Q(status='pending', charges_confirmed=True, pk__in=listed_ids)
        ).filter(pk__in=listed_ids, is_deleted=False).prefetch_related('variants', 'category')
    else:
        qs = Product.objects.none()
    return qs


def _no_distributor_response(request):
    """Show a friendly page when customer has no distributor linked."""
    return render(request, 'products/no_distributor.html', status=200)


def _enrich_product(product, distributor):
    """Attach distributor pricing to product and all its variants."""
    active_variants = list(product.variants.filter(is_active=True).order_by('selling_price'))

    # Multi-variant hint for the catalogue card: 2+ active, named variant-types.
    try:
        named_types = [
            vt for vt in product.variant_types.filter(is_active=True)
            if (vt.name or '').strip() and vt.packs.filter(is_active=True).exists()
        ]
        product.has_named_variants = len(named_types) >= 2
        product.variant_count = len(named_types)
    except Exception:
        product.has_named_variants = False
        product.variant_count = 0

    if distributor:
        from distributors.models import DistributorVariantPrice
        from stockist.models import Stockist, get_stockist_price, stockist_for

        active_stockist = stockist_for(distributor)
        dist_prices = {
            dvp.variant_id: dvp
            for dvp in DistributorVariantPrice.objects.filter(
                distributor=distributor,
                variant__product=product,
                is_active=True,
            )
        }

        for v in active_variants:
            dvp = dist_prices.get(v.pk)
            floor_price = v.selling_price
            if active_stockist:
                floor_price, _ = get_stockist_price(product, v, active_stockist, enterprise=distributor)

            if dvp:
                # Never show a customer price below current stockist cost.
                v.display_price = max(dvp.selling_price, floor_price)
                v.display_mrp = v.mrp
                if v.mrp and v.mrp > v.display_price:
                    v.display_discount = int(((v.mrp - v.display_price) / v.mrp) * 100)
                else:
                    v.display_discount = 0
            else:
                v.display_price = max(v.selling_price, floor_price)
                v.display_mrp = v.mrp
                v.display_discount = v.discount_percent()

        # Default variant = cheapest that has a distributor price
        default = None
        for v in active_variants:
            if v.pk in dist_prices:
                default = v
                default._dist_price = dist_prices[v.pk]
                break
        if not default and active_variants:
            default = active_variants[0]
        product.enriched_default = default
    else:
        for v in active_variants:
            v.display_price = v.selling_price
            v.display_mrp = v.mrp
            v.display_discount = v.discount_percent()
        product.enriched_default = active_variants[0] if active_variants else None

    # Cache enriched variants so template doesn't re-query
    product.enriched_variants = active_variants
    return product


def _requires_login(request):
    """Returns True if this request should be blocked (not logged in and no distributor in session)."""
    if request.user.is_authenticated:
        return False
    # Allow guests who arrived via distributor invite link
    if request.session.get('distributor_id'):
        return False
    return True


def home(request):
    if not request.user.is_authenticated:
        # Check if guest has a distributor via invite link
        distributor = get_distributor(request)
        if distributor:
            categories = Category.objects.all()
            products = list(_product_qs(distributor).order_by('-rating')[:8])
            products = [_enrich_product(p, distributor) for p in products]
            if distributor.hide_out_of_stock_products:
                products = [
                    p for p in products
                    if any((v.stock or 0) > 0 for v in getattr(p, 'enriched_variants', []))
                ]
            return render(request, 'products/home.html', {
                'categories': categories,
                'featured': products,
                'distributor': distributor,
                'is_guest': True,
            })
        return render(request, 'products/landing.html')

    active_role = request.session.get('active_role', request.user.role)
    allowed = request.user.get_roles_list()

    if active_role == 'supplier' and 'supplier' in allowed:
        return redirect('suppliers:dashboard')
    if active_role == 'stockist' and 'stockist' in allowed:
        return redirect('stockist:dashboard')
    if active_role == 'distributor' and 'distributor' in allowed:
        return redirect('distributors:dashboard')
    if active_role == 'wholesaler' and 'wholesaler' in allowed:
        return redirect('wholesaler:dashboard')
    if (active_role == 'admin' or request.user.is_superuser) and ('admin' in allowed or request.user.is_superuser):
        return redirect('admin_panel:dashboard')

    # Customer
    distributor = get_distributor(request)
    if not distributor:
        return _no_distributor_response(request)
    categories = Category.objects.all()
    products = list(_product_qs(distributor).order_by('-rating')[:8])
    products = [_enrich_product(p, distributor) for p in products]
    if distributor.hide_out_of_stock_products:
        products = [
            p for p in products
            if any((v.stock or 0) > 0 for v in getattr(p, 'enriched_variants', []))
        ]
    return render(request, 'products/home.html', {
        'categories': categories,
        'featured': products,
        'distributor': distributor,
    })


def product_list(request):
    if _requires_login(request):
        messages.info(request, 'Please use a distributor invite link to browse products.')
        return redirect('accounts:login')

    from django.core.paginator import Paginator
    from django.conf import settings

    distributor = get_distributor(request)
    if not distributor:
        if request.user.is_authenticated:
            return _no_distributor_response(request)
        messages.info(request, 'Please use a distributor invite link to browse products.')
        return redirect('products:home')
    products = _product_qs(distributor)
    categories = Category.objects.all()

    category_slug = request.GET.get('category')
    search = request.GET.get('q', '').strip()
    sort = request.GET.get('sort', 'newest')

    if category_slug:
        products = products.filter(category__slug=category_slug)
    if search:
        from django.db.models import Q
        products = products.filter(
            Q(name__icontains=search) |
            Q(description__icontains=search) |
            Q(category__name__icontains=search) |
            Q(variants__label__icontains=search) |
            Q(supplier__company_name__icontains=search)
        ).distinct()

    # Apply DB-level sorting before materializing to limit memory usage
    if sort == 'rating':
        products = products.order_by('-rating')
    else:
        products = products.order_by('-created_at')

    # Cap the queryset before materializing to avoid loading entire catalog
    MAX_PRODUCTS = 500
    products = list(dict.fromkeys(products[:MAX_PRODUCTS]))
    products = [_enrich_product(p, distributor) for p in products]

    def _get_price(p):
        v = getattr(p, 'enriched_default', None)
        if not v: return float('inf')
        return float(getattr(v, 'display_price', v.selling_price))

    # Base filter for invalid products without price
    products = [p for p in products if _get_price(p) != float('inf')]

    if distributor.hide_out_of_stock_products:
        products = [
            p for p in products
            if any((v.stock or 0) > 0 for v in getattr(p, 'enriched_variants', []))
        ]

    min_price = request.GET.get('min_price')
    max_price = request.GET.get('max_price')

    if min_price:
        try:
            min_val = float(min_price)
            products = [p for p in products if _get_price(p) >= min_val]
        except ValueError:
            pass

    if max_price:
        try:
            max_val = float(max_price)
            products = [p for p in products if _get_price(p) <= max_val]
        except ValueError:
            pass

    if sort == 'price_asc':
        products.sort(key=_get_price)
    elif sort == 'price_desc':
        products.sort(key=_get_price, reverse=True)
    # rating and newest already sorted at DB level

    paginator = Paginator(products, getattr(settings, 'PRODUCTS_PER_PAGE', 12))
    page = paginator.get_page(request.GET.get('page'))

    return render(request, 'products/product_list.html', {
        'products': page,
        'page_obj': page,
        'categories': categories,
        'selected_category': category_slug,
        'search': search,
        'sort': sort,
        'distributor': distributor,
    })


def product_detail(request, pk):
    if _requires_login(request):
        messages.info(request, 'Please use a distributor invite link to view products.')
        return redirect('accounts:login')

    distributor = get_distributor(request)
    if not distributor:
        if request.user.is_authenticated:
            return _no_distributor_response(request)
        return redirect('products:home')
    from django.db.models import Q
    # all_objects + ownership check: a shop's own product detail must render for
    # its own customers; direct-supply products carry no stockist charges.
    product = get_object_or_404(
        Product.all_objects.visible_to_enterprise(distributor).filter(
            Q(charges_confirmed=True) | Q(direct_supply=True) | Q(owner_enterprise=distributor),
            is_deleted=False,
        ), pk=pk
    )
    if product.status not in ('approved',) and not (product.status == 'pending' and product.charges_confirmed):
        from django.http import Http404
        raise Http404

    variants = list(
        product.variants.filter(is_active=True)
        .select_related('variant_type')
        .order_by('variant_type__position', 'selling_price')
    )
    variants_data = []
    from stockist.models import Stockist, get_stockist_price, stockist_for
    active_stockist = stockist_for(distributor)
    for v in variants:
        price = float(v.selling_price)
        if active_stockist:
            floor_price, _ = get_stockist_price(product, v, active_stockist, enterprise=distributor)
            price = float(max(v.selling_price, floor_price))
        mrp = float(v.mrp) if v.mrp else 0
        if distributor:
            dvp = v.dist_prices.filter(distributor=distributor, is_active=True).first()
            if dvp:
                # Keep customer price >= current stockist cost.
                price = max(price, float(dvp.selling_price))
        disc = int(((mrp - price) / mrp) * 100) if mrp and mrp > price else 0
        variants_data.append({
            'id': v.id, 'label': v.label, 'mrp': mrp,
            'selling_price': price, 'discount': disc, 'stock': v.stock,
            'variant_type_id': v.variant_type_id or 0,
            'variant_type_name': (v.variant_type.name if v.variant_type else '') or '',
        })

    # Ordered list of named variant-types; a picker shows only when 2+ exist.
    variant_types = [
        {'id': vt.pk, 'name': vt.name}
        for vt in product.variant_types.filter(is_active=True).order_by('position', 'id')
        if vt.packs.filter(is_active=True).exists()
    ]
    show_variant_picker = sum(1 for vt in variant_types if vt['name']) >= 1 and len(variant_types) > 1

    related = [_enrich_product(p, distributor) for p in
               _product_qs(distributor).filter(category=product.category).exclude(pk=pk)[:4]]

    can_review = False
    if request.user.is_authenticated and request.user.role == 'customer':
        from orders.models import OrderItem
        can_review = OrderItem.objects.filter(
            order__customer=request.user,
            order__status='delivered',
            product=product,
        ).exists()

    return render(request, 'products/product_detail.html', {
        'product': product,
        'variants': variants,
        'variants_data': variants_data,
        'variants_json': json.dumps(variants_data),
        'variant_types': variant_types,
        'show_variant_picker': show_variant_picker,
        'related': related,
        'distributor': distributor,
        'can_review': can_review,
    })


def set_distributor(request, invite_code):
    from distributors.models import Distributor
    try:
        dist = Distributor.objects.get(invite_code=invite_code, status='active')
        request.session['distributor_id'] = dist.pk
        request.session.modified = True

        # Permanently link a logged-in customer to this shop (even post-signup),
        # so they appear in the shop's "My Customers" list. Only link when the
        # account has no shop yet, so we never move a customer between shops.
        user = request.user
        if (user.is_authenticated and getattr(user, 'role', None) == 'customer'
                and not user.distributor_id):
            user.distributor = dist
            user.save(update_fields=['distributor'])
            try:
                from distributors.rewards import give_welcome_bonus
                give_welcome_bonus(user, dist)
            except Exception:
                pass

        messages.success(request, f'Welcome! You are shopping from {dist.company_name}.')
    except Distributor.DoesNotExist:
        messages.error(request, 'Invalid distributor link.')
    return redirect('products:home')


def search_autocomplete(request):
    """Return JSON list of product names, prices, and images matching query."""
    from django.http import JsonResponse
    from django.db.models import Q
    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse([], safe=False)
    distributor = get_distributor(request)
    qs = _product_qs(distributor).filter(
        Q(name__icontains=q) |
        Q(category__name__icontains=q) |
        Q(supplier__company_name__icontains=q)
    ).distinct()[:8]
    
    results = []
    for p in qs:
        ep = _enrich_product(p, distributor)
        v = getattr(ep, 'enriched_default', None)
        price = "—"
        if v:
            price = float(getattr(v, 'display_price', v.selling_price))
        results.append({
            'name': p.name,
            'url': f'/products/{p.pk}/',
            'image': p.image.url if p.image else '',
            'price': price,
        })
    return JsonResponse(results, safe=False)



def add_review(request, pk):
    """Submit or update a product review by the logged-in customer."""
    from .models import ProductReview
    from orders.models import OrderItem

    if not request.user.is_authenticated:
        messages.info(request, 'Please login to submit a review.')
        return redirect('accounts:login')

    if request.method != 'POST':
        return redirect('products:product_detail', pk=pk)

    product = get_object_or_404(Product, pk=pk)

    has_purchased = OrderItem.objects.filter(
        order__customer=request.user,
        order__status='delivered',
        product=product,
    ).exists()
    if not has_purchased:
        messages.error(request, 'Review is allowed only after your order is delivered.')
        return redirect('products:product_detail', pk=pk)

    try:
        rating = int(request.POST.get('rating') or 0)
    except (TypeError, ValueError):
        rating = 0

    comment = (request.POST.get('comment') or '').strip()

    if rating < 1 or rating > 5:
        messages.error(request, 'Please provide a valid rating between 1 and 5.')
        return redirect('products:product_detail', pk=pk)

    ProductReview.objects.update_or_create(
        product=product,
        user=request.user,
        defaults={'rating': rating, 'comment': comment, 'is_verified_purchase': True}
    )

    aggs = product.reviews.aggregate(a=Avg('rating'), c=Count('id'))
    product.rating = round(aggs['a'] or 0, 1)
    product.review_count = aggs['c'] or 0
    product.save(update_fields=['rating', 'review_count'])

    messages.success(request, 'Review submitted.')
    return redirect('products:product_detail', pk=pk)


def distributor_storefront(request, invite_code):
    """
    Public storefront for a distributor — no login required.
    URL: /store/<invite_code>/
    Shareable link that shows only this distributor's listed products.
    """
    from distributors.models import Distributor
    from django.core.paginator import Paginator
    from django.conf import settings

    try:
        distributor = Distributor.objects.get(invite_code__iexact=invite_code, status='active')
    except Distributor.DoesNotExist:
        messages.error(request, 'This store link is invalid or the store is currently unavailable.')
        return redirect('products:home')

    # Set session so cart, branding, and navigation work
    request.session['distributor_id'] = distributor.pk
    request.session.modified = True

    # Permanently link a logged-in customer to this shop when they open its
    # store link (even post-signup), so they show up in "My Customers". Only
    # link when the account has no shop yet — never move a customer between shops.
    user = request.user
    if (user.is_authenticated and getattr(user, 'role', None) == 'customer'
            and not user.distributor_id):
        user.distributor = distributor
        user.save(update_fields=['distributor'])
        try:
            from distributors.rewards import give_welcome_bonus
            give_welcome_bonus(user, distributor)
        except Exception:
            pass

    products = _product_qs(distributor)
    categories = Category.objects.all()

    category_slug = request.GET.get('category')
    search = request.GET.get('q', '').strip()
    sort = request.GET.get('sort', 'newest')

    if category_slug:
        products = products.filter(category__slug=category_slug)
    if search:
        from django.db.models import Q
        products = products.filter(
            Q(name__icontains=search) | Q(category__name__icontains=search)
        ).distinct()

    if sort == 'rating':
        products = products.order_by('-rating')
    else:
        products = products.order_by('-created_at')

    products = list(dict.fromkeys(products[:500]))
    products = [_enrich_product(p, distributor) for p in products]

    def _get_price(p):
        v = getattr(p, 'enriched_default', None)
        if not v: return float('inf')
        return float(getattr(v, 'display_price', v.selling_price))

    products = [p for p in products if _get_price(p) != float('inf')]

    if distributor.hide_out_of_stock_products:
        products = [
            p for p in products
            if any((v.stock or 0) > 0 for v in getattr(p, 'enriched_variants', []))
        ]

    if sort == 'price_asc':
        products.sort(key=_get_price)
    elif sort == 'price_desc':
        products.sort(key=_get_price, reverse=True)

    # Pre-compute template-safe attributes
    for p in products:
        v = getattr(p, 'enriched_default', None)
        p.default_var = v
        if v:
            p.display_price = _get_price(p)
            p.display_label = v.label
            p.display_mrp = float(v.mrp) if v.mrp else 0
            p.has_discount = p.display_mrp > p.display_price if p.display_mrp else False

    paginator = Paginator(products, getattr(settings, 'PRODUCTS_PER_PAGE', 12))
    page = paginator.get_page(request.GET.get('page'))

    return render(request, 'products/storefront.html', {
        'products': page,
        'page_obj': page,
        'categories': categories,
        'selected_category': category_slug,
        'search': search,
        'sort': sort,
        'distributor': distributor,
        'invite_code': invite_code,
    })
