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


def _handle_marketplace_packed(request, w, my_stockist, action):
    """Fill or forward a shop's packed restock request waiting at this Distributor.

    fulfil  : move units from my WholesalerStock into the shop's stock.
    forward : ask my stockist for the units. The quantity is rounded up to the
              stockist's bundle step (kb/10: a bundle order stays a bundle).
              When that request is delivered I hold the stock and can fulfil.
    """
    from django.db import transaction
    from products.bundle_chain import required_multiple, round_up
    from .models import DistributorStock, DistributorStockBatch, RestockRequest
    from .wholesaler_models import WholesalerRestockRequest, WholesalerStock
    try:
        with transaction.atomic():
            shop_request = RestockRequest.objects.select_for_update().select_related(
                'variant__product', 'distributor'
            ).get(
                pk=request.POST.get('request_id'),
                distributor__wholesaler=w,
                status='awaiting_stock',
                loose_grams=0,
            )
            variant = shop_request.variant
            qty = shop_request.quantity_requested
            if action == 'fulfil_marketplace_packed':
                own = WholesalerStock.objects.select_for_update().filter(
                    wholesaler=w, variant=variant,
                ).first()
                available = own.quantity if own else 0
                if available < qty:
                    raise ValueError(
                        f'Only {available} × {variant.label} in your stock; {qty} are needed. '
                        'Forward the request to your stockist instead.'
                    )
                own.quantity -= qty
                own.save(update_fields=['quantity'])
                from distributors.shop_blocking import release_shop_request_block
                release_shop_request_block(shop_request)
                from lakrish_mart.activity_logger import act as _act
                _act.stock('distributor_filled_shop_request', variant=variant, qty=qty,
                           by=request.user, detail=f'shop {shop_request.distributor.company_name}')
                stock_obj, _ = DistributorStock.objects.get_or_create(
                    distributor=shop_request.distributor, variant=variant,
                    defaults={'quantity': 0},
                )
                DistributorStockBatch.objects.create(
                    stock=stock_obj, quantity=qty, cost_price=variant.selling_price,
                    note=f'Restock request #{shop_request.pk} filled by Distributor',
                )
                stock_obj.refresh_quantity()
                shop_request.status = 'delivered'
                shop_request.save(update_fields=['status', 'updated_at'])
                messages.success(
                    request,
                    f'{qty} × {variant.label} of {variant.product.name} delivered to '
                    f'{shop_request.distributor.company_name}.'
                )
                return
            # forward
            from distributors.shop_blocking import release_shop_request_block
            release_shop_request_block(shop_request)
            from lakrish_mart.activity_logger import act as _act
            _act.stock('distributor_forwarded_shop_request', variant=variant, qty=qty,
                       by=request.user, detail=f'shop {shop_request.distributor.company_name}')
            if my_stockist is None:
                raise ValueError('No active stockist is assigned to you.')
            if WholesalerRestockRequest.objects.filter(
                marketplace_request=shop_request, status='pending',
            ).exists():
                raise ValueError('A stockist request is already open for this shop request.')
            upstream_qty = round_up(qty, required_multiple(w, variant))
            from stockist.supply_requests import create_wholesaler_request
            upstream = create_wholesaler_request(
                w, my_stockist, variant, upstream_qty, loose_grams=0,
                marketplace_request=shop_request,
                note=f'For shop request #{shop_request.pk}',
            )
            shop_request.upstream_request = upstream
            shop_request.save(update_fields=['upstream_request', 'updated_at'])
            extra = upstream_qty - qty
            messages.success(
                request,
                f'{upstream_qty} × {variant.label} requested from {my_stockist.company_name}.'
                + (f' {extra} extra stay in your stock (bundle size).' if extra else '')
            )
    except RestockRequest.DoesNotExist:
        messages.error(request, 'Shop restock request not found.')
    except ValueError as exc:
        messages.error(request, str(exc))


@wholesaler_required
def bundle_settings(request):
    """Distributor's own bundle rules and the no-buffer toggle (kb/10)."""
    from products.bundle_chain import handle_settings_post, settings_context
    w = request.user.wholesaler_profile
    if request.method == 'POST':
        try:
            messages.success(request, handle_settings_post(request, w))
        except ValueError as exc:
            messages.error(request, str(exc))
        return redirect('wholesaler:bundle_settings')
    ctx = settings_context(w)
    ctx.update({
        'wholesaler': w,
        'tier_label': 'Distributor',
        'buyer_label': 'your shops (Marketplaces)',
        'forward_label': 'your stockist',
    })
    return render(request, 'distributors/wholesaler_bundle_settings.html', ctx)


@wholesaler_required
def my_stock(request):
    """Stage 7: the Distributor's own stock ledger.

    Stock rules (same as stockist and marketplace):
      * increase ONLY via a delivered restock request to its ONE stockist;
      * manual changes can only REDUCE (offline sales, own use, wastage).
    """
    from products.models import ProductVariant
    from .wholesaler_models import (
        WholesalerLooseStock, WholesalerStock, WholesalerRestockRequest,
    )

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

        elif action == 'restock' and w.no_buffer_stock:
            messages.error(
                request,
                'No-buffer policy is ON: this Distributor does not keep stock. Shop requests '
                'go straight to your stockist.'
            )
        elif action == 'restock':
            if my_stockist is None:
                messages.error(
                    request,
                    'No stockist is assigned to you yet — ask the admin to set '
                    'your stockist first.'
                )
            else:
                created = 0
                rejected = 0
                blocked_by_stock = 0
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
                            supplier_can_supply=True,
                            product__status='approved', product__is_deleted=False,
                            product__direct_supply=False,
                        ).first()
                        if variant:
                            from products.bundle_chain import required_multiple
                            if qty % required_multiple(w, variant):
                                rejected += 1
                                continue
                            from stockist.supply_requests import RequestBlocked, create_wholesaler_request
                            try:
                                create_wholesaler_request(
                                    w, my_stockist, variant, qty, loose_grams=0,
                                )
                            except RequestBlocked:
                                blocked_by_stock += 1
                                continue
                            created += 1
                if blocked_by_stock:
                    messages.warning(
                        request,
                        f'{blocked_by_stock} request(s) not sent: {my_stockist.company_name} has '
                        'no free stock of those packs right now.'
                    )
                if created:
                    messages.success(request, f'{created} restock request(s) sent to {my_stockist.company_name}.')
                    if rejected:
                        messages.warning(
                            request,
                            f'{rejected} request(s) were skipped because the quantity did not '
                            'match the supplier order multiple or the loose-stock setting is invalid.'
                        )
                elif rejected:
                    messages.error(
                        request,
                        'No requests were sent. Check the supplier order multiple and loose-stock setting.'
                    )
                else:
                    messages.warning(request, 'Enter a quantity for at least one pack.')

        elif action == 'restock_loose_for_marketplace':
            from django.db import transaction
            from distributors.models import RestockRequest
            try:
                with transaction.atomic():
                    marketplace_request = RestockRequest.objects.select_for_update().select_related(
                        'variant__product'
                    ).get(
                        pk=request.POST.get('request_id'),
                        distributor__wholesaler=w,
                        status='awaiting_stock',
                        loose_grams__gt=0,
                    )
                    amount = int(request.POST.get('loose_grams', '0'))
                    if amount < marketplace_request.loose_grams:
                        raise ValueError(
                            f'Request at least {marketplace_request.loose_grams} base units '
                            'to cover the Marketplace request.'
                        )
                    if my_stockist is None:
                        raise ValueError('No active stockist is assigned to this Distributor.')
                    product = marketplace_request.variant.product
                    if not product.allow_loose_distribution:
                        raise ValueError('This product is not enabled for loose-stock supply.')
                    source_variant = ProductVariant.objects.shared().filter(
                        product=product,
                        is_active=True,
                        supplier_can_supply=True,
                        unit_grams__gt=0,
                        product__status='approved',
                        product__is_deleted=False,
                        product__direct_supply=False,
                    ).order_by('unit_grams').first()
                    if source_variant is None:
                        raise ValueError('No supplier-enabled measurable pack is available for this product.')
                    if WholesalerRestockRequest.objects.filter(
                        marketplace_request=marketplace_request,
                        status='pending',
                    ).exists():
                        raise ValueError('An upstream stock request is already open for this shop request.')
                    from products.bundle_chain import required_multiple, round_up
                    quantity = round_up(
                        (amount + source_variant.unit_grams - 1) // source_variant.unit_grams,
                        required_multiple(w, source_variant),
                    )
                    # The Distributor no longer holds these grams for the shop request, and
                    # the stockist now holds them for this distributor request (kb/13).
                    from distributors.shop_blocking import release_shop_request_block
                    release_shop_request_block(marketplace_request)
                    from stockist.supply_requests import block_loose
                    block_loose(my_stockist, source_variant.product, amount)
                    upstream_request = WholesalerRestockRequest.objects.create(
                        wholesaler=w,
                        stockist=my_stockist,
                        variant=source_variant,
                        quantity_requested=quantity,
                        loose_grams=amount,
                        marketplace_request=marketplace_request,
                        note=f'Loose stock for Marketplace request #{marketplace_request.pk}',
                    )
                    marketplace_request.upstream_request = upstream_request
                    marketplace_request.save(update_fields=['upstream_request', 'updated_at'])
                messages.success(request, f'{amount} base units requested from {my_stockist.company_name}.')
            except RestockRequest.DoesNotExist:
                messages.error(request, 'Marketplace restock request not found.')
            except ValueError as exc:
                messages.error(request, str(exc))

        elif action == 'buffer_setting':
            # kb/10: no buffer = shop requests skip me and go to the stockist.
            w.no_buffer_stock = request.POST.get('no_buffer_stock') == '1'
            w.save(update_fields=['no_buffer_stock'])
            if w.no_buffer_stock:
                messages.success(
                    request,
                    'No buffer stock is ON. New shop restock requests (pack and loose) go '
                    'straight to the stockist. Requests already waiting here stay with you.'
                )
            else:
                messages.success(
                    request,
                    'No buffer stock is OFF. Shop restock requests (pack and loose) wait here for you.'
                )
        elif action in ('fulfil_marketplace_packed', 'forward_marketplace_packed'):
            _handle_marketplace_packed(request, w, my_stockist, action)
        elif action == 'fulfil_marketplace_loose':
            from django.db import transaction
            from distributors.models import DistributorLooseStock, RestockRequest
            try:
                with transaction.atomic():
                    marketplace_request = RestockRequest.objects.select_for_update().select_related(
                        'variant__product'
                    ).get(
                        pk=request.POST.get('request_id'),
                        distributor__wholesaler=w,
                        status='awaiting_stock',
                        loose_grams__gt=0,
                    )
                    loose, _ = WholesalerLooseStock.objects.select_for_update().get_or_create(
                        wholesaler=w,
                        product=marketplace_request.variant.product,
                        defaults={'quantity_grams': 0, 'cost_price_per_gram': 0},
                    )
                    if loose.quantity_grams < marketplace_request.loose_grams:
                        raise ValueError(
                            f'Only {loose.quantity_grams} base units are in loose stock; '
                            f'{marketplace_request.loose_grams} are required.'
                        )
                    destination, _ = DistributorLooseStock.objects.select_for_update().get_or_create(
                        distributor=marketplace_request.distributor,
                        product=marketplace_request.variant.product,
                        defaults={
                            'quantity_grams': 0,
                            'cost_price_per_gram': loose.cost_price_per_gram,
                        },
                    )
                    amount = marketplace_request.loose_grams
                    loose.quantity_grams -= amount
                    loose.save(update_fields=['quantity_grams'])
                    from distributors.shop_blocking import release_shop_request_block
                    release_shop_request_block(marketplace_request)
                    destination.quantity_grams += amount
                    destination.cost_price_per_gram = loose.cost_price_per_gram
                    destination.save(update_fields=['quantity_grams', 'cost_price_per_gram'])
                    marketplace_request.status = 'delivered'
                    marketplace_request.save(update_fields=['status', 'updated_at'])
                messages.success(request, f'{amount} base units delivered to the Marketplace.')
            except RestockRequest.DoesNotExist:
                messages.error(request, 'Marketplace restock request not found.')
            except ValueError as exc:
                messages.error(request, str(exc))
        return redirect('wholesaler:my_stock')

    stock_items = WholesalerStock.objects.filter(wholesaler=w).select_related(
        'variant__product__category'
    )
    # Everything orderable from the shared catalogue (direct-supply products
    # never pass through the stockist, so they are not orderable here).
    orderable = ProductVariant.objects.shared().filter(
        is_active=True, product__status='approved',
        product__is_deleted=False, product__direct_supply=False,
        product__charges_confirmed=True, supplier_can_supply=True,
    ).select_related('product__category').order_by('product__name', 'selling_price')

    requests_qs = WholesalerRestockRequest.objects.filter(wholesaler=w).select_related(
        'variant__product', 'stockist'
    )[:30]
    loose_stock_items = WholesalerLooseStock.objects.filter(
        wholesaler=w, quantity_grams__gt=0
    ).select_related('product').order_by('product__name')
    from distributors.models import RestockRequest
    loose_stock_map = {
        row.product_id: row.quantity_grams
        for row in WholesalerLooseStock.objects.filter(wholesaler=w)
    }
    upstream_open_ids = set(WholesalerRestockRequest.objects.filter(
        wholesaler=w, marketplace_request__isnull=False, status='pending'
    ).values_list('marketplace_request_id', flat=True))
    marketplace_loose_requests = [
        {
            'request': row,
            'available': loose_stock_map.get(row.variant.product_id, 0),
            'upstream_open': row.pk in upstream_open_ids,
        }
        for row in RestockRequest.objects.filter(
            distributor__wholesaler=w,
            loose_grams__gt=0,
            status='awaiting_stock',
        ).select_related('distributor', 'variant__product').order_by('-requested_at')
    ]

    own_stock_map = {
        row.variant_id: row.quantity
        for row in WholesalerStock.objects.filter(wholesaler=w)
    }
    marketplace_packed_requests = [
        {
            'request': row,
            'available': own_stock_map.get(row.variant_id, 0),
            'upstream_open': row.pk in upstream_open_ids,
        }
        for row in RestockRequest.objects.filter(
            distributor__wholesaler=w,
            loose_grams=0,
            status='awaiting_stock',
        ).select_related('distributor', 'variant__product').order_by('-requested_at')
    ]
    return render(request, 'distributors/wholesaler_my_stock.html', {
        'wholesaler': w,
        'my_stockist': my_stockist,
        'stock_items': stock_items,
        'orderable': orderable,
        'requests': requests_qs,
        'loose_stock_items': loose_stock_items,
        'marketplace_loose_requests': marketplace_loose_requests,
        'marketplace_packed_requests': marketplace_packed_requests,
    })
