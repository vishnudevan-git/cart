from .session_cart import guest_cart_count


def cart_count(request):
    count = 0
    if request.user.is_authenticated:
        active_role = request.session.get('active_role', request.user.role)
        if active_role == 'customer':
            from .models import Cart
            count = Cart.objects.filter(customer=request.user).count()
    else:
        count = guest_cart_count(request)
    return {'cart_count': count}
