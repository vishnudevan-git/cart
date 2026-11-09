"""Views for the wholesale Distributor role (code: wholesaler, UI: 'Distributor').

A Distributor's main job is to find & create new shops (Marketplaces) and reset
their passwords. It does no processing — it just supplies its shops.
"""
from functools import wraps

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from accounts.decorators import ensure_user_role
from accounts.models import User
from .models import Distributor
from .wholesaler_models import Wholesaler


def wholesaler_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        denial = ensure_user_role(request, ('wholesaler',), set_active_role='wholesaler')
        if denial:
            return denial
        try:
            w = request.user.wholesaler_profile
        except Wholesaler.DoesNotExist:
            messages.error(request, 'Your Distributor profile is missing. Please contact admin.')
            request.session['active_role'] = 'customer'
            return redirect('products:home')
        if w.status != 'active':
            return render(request, 'distributors/wholesaler_suspended.html', {'wholesaler': w})
        return view_func(request, *args, **kwargs)
    return wrapper


@wholesaler_required
def dashboard(request):
    w = request.user.wholesaler_profile
    enterprises = w.enterprises.select_related('user').all()
    active = enterprises.filter(status='active').count()
    ctx = {
        'wholesaler': w,
        'enterprises': enterprises,
        'enterprise_count': enterprises.count(),
        'active_count': active,
    }
    return render(request, 'distributors/wholesaler_dashboard.html', ctx)


@wholesaler_required
def create_shop(request):
    """Distributor creates a new shop (Marketplace) account under itself."""
    w = request.user.wholesaler_profile
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()
        company_name = request.POST.get('company_name', '').strip()
        area = request.POST.get('area', '').strip()
        phone = request.POST.get('phone', '').strip()

        from accounts.utils import is_valid_username
        if not username or not password or not company_name:
            messages.error(request, 'Shop name, login and password are required.')
        elif not is_valid_username(username):
            messages.error(request, 'Login must be a valid mobile number or email ID.')
        elif User.all_objects.filter(username__iexact=username).exists():
            messages.error(request, f'Login "{username}" already exists.')
        else:
            user = User.objects.create_user(
                username=username, password=password, role='distributor', phone=phone,
            )
            shop = Distributor.objects.create(
                user=user, company_name=company_name, area=area,
                wholesaler=w, status='active',
            )
            messages.success(request, f'Shop "{shop.company_name}" created. Invite code: {shop.invite_code}')
            return redirect('wholesaler:dashboard')
        return render(request, 'distributors/wholesaler_create_shop.html', {'post': request.POST})
    return render(request, 'distributors/wholesaler_create_shop.html', {})


@wholesaler_required
def reset_shop_password(request, pk):
    """Distributor resets the password of one of ITS OWN shops."""
    w = request.user.wholesaler_profile
    shop = get_object_or_404(Distributor, pk=pk, wholesaler=w)
    if request.method == 'POST':
        new_password = request.POST.get('new_password', '').strip()
        if len(new_password) < 6:
            messages.error(request, 'Password must be at least 6 characters.')
        else:
            shop.user.set_password(new_password)
            shop.user.save()
            messages.success(request, f'Password reset for "{shop.company_name}".')
            return redirect('wholesaler:dashboard')
    return render(request, 'distributors/wholesaler_reset_password.html', {'shop': shop})


@wholesaler_required
def my_stock(request):
    """Stage 7: the Distributor's own stock ledger.

    Stock rules (same as stockist and marketplace):
      * increase ONLY via a delivered restock request to its ONE stockist;
      * manual changes can only REDUCE (offline sales, own use, wastage).
    """
    from products.models import ProductVariant
    from .wholesaler_models import WholesalerStock, WholesalerRestockRequest

    w = request.user.wholesaler_profile
    my_stockist = w.stockist if (w.stockist and w.stockist.status == 'active') else None

    if request.method == 'POST':
        action = request.POST.get('action', '')

        if action == 'auto_restock_settings':
            # Stage 7g: opt-in auto-restock (threshold + reorder quantity).
            w.auto_restock_enabled = request.POST.get('enabled') == '1'
            try:
                w.auto_restock_threshold = max(0, int(request.POST.get('threshold', 5)))
                w.auto_restock_quantity = max(1, int(request.POST.get('quantity', 10)))
            except ValueError:
                pass
            try:
                from decimal import Decimal as _D, InvalidOperation as _IO
                raw_cap = (request.POST.get('max_price') or '0').strip()
                w.auto_restock_max_price = max(_D('0'), _D(raw_cap or '0'))
            except (_IO, ValueError):
                pass
            w.save(update_fields=['auto_restock_enabled', 'auto_restock_threshold',
                                  'auto_restock_quantity', 'auto_restock_max_price'])
            if w.auto_restock_enabled and my_stockist is None:
                messages.warning(
                    request,
                    'Auto-restock is ON but you have no assigned stockist — '
                    'ask the admin to set one, otherwise nothing can be ordered.'
                )
            elif w.auto_restock_enabled:
                from stockist.auto_restock import check_wholesaler
                made = check_wholesaler(w)
                messages.success(
                    request,
                    f'Auto-restock ON — at ≤{w.auto_restock_threshold} units a request for '
                    f'{w.auto_restock_quantity} units goes to {my_stockist.company_name}.'
                    + (f' {made} request(s) filed right now.' if made else '')
                )
            else:
                messages.success(request, 'Auto-restock turned off.')
            return redirect('wholesaler:my_stock')

        if action == 'toggle_auto':
            # Per-item auto-restock switch.
            stock = WholesalerStock.objects.filter(
                pk=request.POST.get('stock_id'), wholesaler=w
            ).first()
            if stock:
                stock.auto_restock_exempt = not stock.auto_restock_exempt
                stock.save(update_fields=['auto_restock_exempt'])
                state = 'excluded from' if stock.auto_restock_exempt else 'included in'
                messages.success(request, f'{stock.variant} is now {state} auto-restock.')
            return redirect('wholesaler:my_stock')

        if action == 'reduce':
            stock_id = request.POST.get('stock_id')
            try:
                units = int(request.POST.get('units', '0'))
            except ValueError:
                units = 0
            stock = WholesalerStock.objects.filter(pk=stock_id, wholesaler=w).first()
            if not stock or units <= 0:
                messages.error(request, 'Enter how many units to reduce.')
            elif units > stock.quantity:
                messages.error(
                    request,
                    f'You only have {stock.quantity} units. Stock can only be '
                    'reduced manually — increases come from stockist deliveries.'
                )
            else:
                stock.quantity -= units
                stock.save()
                messages.success(request, f'Reduced {stock.variant} by {units} unit(s).')
                from stockist.auto_restock import check_wholesaler
                made = check_wholesaler(w)
                if made:
                    messages.info(request, f'Auto-restock: {made} request(s) filed with your stockist.')

        elif action == 'restock':
            if my_stockist is None:
                messages.error(
                    request,
                    'No stockist is assigned to you yet — ask the admin to set '
                    'your stockist first.'
                )
            else:
                created = 0
                for key, val in request.POST.items():
                    if key.startswith('qty_') and val.strip():
                        try:
                            qty = int(val.strip())
                        except ValueError:
                            continue
                        if qty <= 0:
                            continue
                        variant = ProductVariant.objects.shared().filter(
                            pk=key[4:], is_active=True,
                            product__status='approved', product__is_deleted=False,
                            product__direct_supply=False,
                        ).first()
                        if variant:
                            WholesalerRestockRequest.objects.create(
                                wholesaler=w, stockist=my_stockist,
                                variant=variant, quantity_requested=qty,
                            )
                            created += 1
                if created:
                    messages.success(request, f'{created} restock request(s) sent to {my_stockist.company_name}.')
                else:
                    messages.warning(request, 'Enter a quantity for at least one pack.')
        return redirect('wholesaler:my_stock')

    stock_items = WholesalerStock.objects.filter(wholesaler=w).select_related(
        'variant__product__category'
    )
    # Everything orderable from the shared catalogue (direct-supply products
    # never pass through the stockist, so they are not orderable here).
    orderable = ProductVariant.objects.shared().filter(
        is_active=True, product__status='approved',
        product__is_deleted=False, product__direct_supply=False,
        product__charges_confirmed=True,
    ).select_related('product__category').order_by('product__name', 'selling_price')

    requests_qs = WholesalerRestockRequest.objects.filter(wholesaler=w).select_related(
        'variant__product', 'stockist'
    )[:30]

    return render(request, 'distributors/wholesaler_my_stock.html', {
        'wholesaler': w,
        'my_stockist': my_stockist,
        'stock_items': stock_items,
        'orderable': orderable,
        'requests': requests_qs,
    })
