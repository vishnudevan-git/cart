"""
Guest cart stored in session as:
  request.session['guest_cart'] = { str(variant_pk): quantity, ... }
"""

SESSION_KEY = 'guest_cart'


def get_guest_cart(request):
    return request.session.get(SESSION_KEY, {})


def save_guest_cart(request, cart):
    request.session[SESSION_KEY] = cart
    request.session.modified = True


def guest_add(request, variant_pk, qty=1):
    from products.models import ProductVariant
    cart = get_guest_cart(request)
    key = str(variant_pk)
    new_qty = cart.get(key, 0) + qty
    # Cap to available stock
    try:
        variant = ProductVariant.objects.get(pk=int(variant_pk), is_active=True)
        new_qty = min(new_qty, variant.stock)
    except ProductVariant.DoesNotExist:
        pass
    cart[key] = max(1, new_qty)
    save_guest_cart(request, cart)
    return len(cart)


def guest_update(request, variant_pk, qty):
    from products.models import ProductVariant
    cart = get_guest_cart(request)
    key = str(variant_pk)
    if qty <= 0:
        cart.pop(key, None)
    else:
        # Cap to available stock
        try:
            variant = ProductVariant.objects.get(pk=int(variant_pk), is_active=True)
            qty = min(qty, variant.stock)
        except ProductVariant.DoesNotExist:
            pass
        cart[key] = qty
    save_guest_cart(request, cart)


def guest_remove(request, variant_pk):
    cart = get_guest_cart(request)
    cart.pop(str(variant_pk), None)
    save_guest_cart(request, cart)


def guest_cart_count(request):
    return len(get_guest_cart(request))


def merge_guest_cart_to_user(request, user):
    """Called after login/register — moves session cart into DB."""
    from products.models import ProductVariant
    from .models import Cart

    guest = get_guest_cart(request)
    if not guest:
        return

    for variant_pk, qty in guest.items():
        try:
            variant = ProductVariant.objects.get(pk=int(variant_pk), is_active=True)
        except ProductVariant.DoesNotExist:
            continue

        qty = min(qty, variant.stock)
        if qty <= 0:
            continue

        item, created = Cart.objects.get_or_create(
            customer=user, variant=variant,
            defaults={'quantity': qty}
        )
        if not created:
            item.quantity = min(item.quantity + qty, variant.stock)
            item.save()

    # Clear session cart after merge
    request.session.pop(SESSION_KEY, None)
    request.session.modified = True
