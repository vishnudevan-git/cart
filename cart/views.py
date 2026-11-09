import logging

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import ensure_csrf_cookie
from products.models import ProductVariant
from .models import Cart

logger = logging.getLogger(__name__)
from .session_cart import (
    get_guest_cart, guest_add, guest_update, guest_remove,
    guest_cart_count,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _is_ajax(request):
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


def _is_logged_customer(request):
    if not request.user.is_authenticated:
        return False
    active = request.session.get('active_role', request.user.primary_role())
    return active == 'customer' or request.user.has_role('customer')


def _get_session_distributor(request):
    """Get distributor from session for guest users."""
    dist_id = request.session.get('distributor_id')
    if dist_id:
        from distributors.models import Distributor
        try:
            return Distributor.objects.get(pk=dist_id, status='active')
        except Distributor.DoesNotExist:
            pass
    return None


def _shopper_enterprise(request):
    """The Marketplace (shop) this shopper is buying from — customer link first,
    then the guest's session store."""
    if _is_logged_customer(request):
        dist = getattr(request.user, 'distributor', None)
        if dist:
            return dist
    return _get_session_distributor(request)


def _shoppable_products(request):
    """Products this shopper may put in a basket.

    Shared marketplace products, PLUS the own products of the shop they are
    buying from — never another shop's private own-products.
    """
    from django.db.models import Q
    from products.models import Product as _Product
    return _Product.all_objects.visible_to_enterprise(_shopper_enterprise(request)).filter(
        Q(status='approved') | Q(status='pending', charges_confirmed=True)
    )


def _variant_price(variant, distributor):
    """Get the correct price for a variant — distributor price if available, else base price."""
    if distributor:
        dvp = variant.dist_prices.filter(distributor=distributor, is_active=True).first()
        if dvp:
            return dvp.selling_price
    return variant.selling_price


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ── views ─────────────────────────────────────────────────────────────────────

def cart_view(request):
    """Works for both guests (session) and logged-in customers (DB)."""
    if _is_logged_customer(request):
        distributor = request.user.distributor
        items = Cart.objects.filter(customer=request.user).select_related('variant__product')
        total = sum(i.subtotal(distributor) for i in items)
        return render(request, 'cart/cart.html', {
            'items': items, 'total': total, 'is_guest': False, 'distributor': distributor,
        })

    # Guest — build a list of pseudo-items from session
    distributor = _get_session_distributor(request)
    guest = get_guest_cart(request)
    items = []
    total = 0
    for vpk, qty in guest.items():
        try:
            variant = ProductVariant.objects.select_related('product').get(pk=int(vpk))
            price = _variant_price(variant, distributor)
            subtotal = price * qty
            items.append({
                'variant': variant, 'quantity': qty,
                'unit_price': price, 'subtotal': subtotal, 'pk': vpk,
            })
            total += subtotal
        except ProductVariant.DoesNotExist:
            pass
    return render(request, 'cart/cart.html', {
        'items': items, 'total': total, 'is_guest': True, 'distributor': distributor,
    })


@require_POST
def add_to_cart(request, variant_pk):
    """No login required. Guests use session, customers use DB."""
    ajax = _is_ajax(request)
    from products.models import Product as _Product
    allowed_products = _shoppable_products(request)
    variant = get_object_or_404(ProductVariant, pk=variant_pk, is_active=True, product__in=allowed_products)

    if not variant.is_in_stock():
        if ajax:
            return JsonResponse({'success': False, 'error': 'Out of stock.'})
        messages.error(request, 'This variant is out of stock.')
        return redirect(request.META.get('HTTP_REFERER', 'products:home'))

    qty = max(1, _safe_int(request.POST.get('quantity', 1), 1))
    qty = min(qty, variant.stock)

    if _is_logged_customer(request):
        item, created = Cart.objects.get_or_create(
            customer=request.user, variant=variant, defaults={'quantity': qty}
        )
        if not created:
            item.quantity = min(item.quantity + qty, variant.stock)
            item.save()
        count = Cart.objects.filter(customer=request.user).count()
        # Activity log
        try:
            from lakrish_mart.activity_logger import act as _act
            _act.cart('added', user=request.user, variant=variant, qty=qty)
        except Exception:
            pass
    else:
        count = guest_add(request, variant_pk, qty)

    if ajax:
        return JsonResponse({
            'success': True,
            'cart_count': count,
            'message': f'{variant.product.name} ({variant.label}) added to cart.',
        })

    messages.success(request, f'"{variant.product.name} ({variant.label})" added to cart.')
    return redirect(request.META.get('HTTP_REFERER', 'cart:cart'))


@require_POST
def update_cart(request, pk):
    qty = _safe_int(request.POST.get('quantity', 1), 1)

    if _is_logged_customer(request):
        item = get_object_or_404(Cart, pk=pk, customer=request.user)
        if qty > 0 and qty <= item.variant.stock:
            item.quantity = qty
            item.save()
        elif qty <= 0:
            item.delete()
    else:
        # pk is variant_pk for guests
        guest_update(request, str(_safe_int(pk, 0)), qty)

    return redirect('cart:cart')


@require_POST
def remove_from_cart(request, pk):
    if _is_logged_customer(request):
        item = get_object_or_404(Cart, pk=pk, customer=request.user)
        item.delete()
    else:
        guest_remove(request, str(_safe_int(pk, 0)))

    messages.success(request, 'Item removed.')
    return redirect('cart:cart')


@ensure_csrf_cookie
def cart_state_api(request):
    """Returns a dictionary of variant_pk -> quantity for the current user's cart."""
    if _is_logged_customer(request):
        items = Cart.objects.filter(customer=request.user).values_list('variant_id', 'quantity')
        return JsonResponse(dict(items))
    guest = get_guest_cart(request)
    return JsonResponse({int(k): v for k, v in guest.items()})


@require_POST
def set_qty_api(request, variant_pk):
    """AJAX api to set exact quantity of a variant in cart."""
    if not _is_ajax(request):
        return JsonResponse({'error': 'bad req'}, status=400)
    try:
        qty = _safe_int(request.POST.get('quantity', 0), 0)
        allowed_products = _shoppable_products(request)
        variant = get_object_or_404(ProductVariant, pk=variant_pk, is_active=True, product__in=allowed_products)
        qty = max(0, min(qty, variant.stock))

        if _is_logged_customer(request):
            if qty > 0:
                Cart.objects.update_or_create(customer=request.user, variant=variant, defaults={'quantity': qty})
            else:
                Cart.objects.filter(customer=request.user, variant=variant).delete()
            count = Cart.objects.filter(customer=request.user).count()
        else:
            if qty > 0:
                guest_update(request, str(variant_pk), qty)
            else:
                guest_remove(request, str(variant_pk))
            count = guest_cart_count(request)

        return JsonResponse({'success': True, 'variant_pk': variant.pk, 'quantity': qty, 'cart_count': count})
    except Exception:
        logger.exception('set_qty_api error for variant_pk=%s', variant_pk)
        return JsonResponse({'error': 'Failed to update cart. Please try again.'}, status=500)


@ensure_csrf_cookie
def cart_offcanvas_view(request):
    """Returns the rendered HTML for the offcanvas cart."""
    if _is_logged_customer(request):
        db_items = Cart.objects.filter(customer=request.user).select_related('variant__product')
        distributor = request.user.distributor
        cart_items = []
        for item in db_items:
            cart_items.append({
                'variant': item.variant,
                'quantity': item.quantity,
                'unit_price': item.unit_price(distributor),
                'subtotal': item.subtotal(distributor),
            })
    else:
        distributor = _get_session_distributor(request)
        guest = get_guest_cart(request)
        variant_ids = guest.keys()
        variants = ProductVariant.objects.filter(pk__in=variant_ids, is_active=True).select_related('product')
        cart_items = []
        for v in variants:
            qty = guest[str(v.pk)]
            price = _variant_price(v, distributor)
            cart_items.append({
                'variant': v,
                'quantity': qty,
                'unit_price': price,
                'subtotal': price * qty,
            })

    subtotal = sum(item['subtotal'] for item in cart_items)

    return render(request, 'cart/offcanvas.html', {
        'cart_items': cart_items,
        'subtotal': subtotal,
        'distributor': distributor,
        'is_logged_in': _is_logged_customer(request)
    })
