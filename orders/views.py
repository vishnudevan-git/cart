from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import models, transaction
from django.db.models import Prefetch
from cart.models import Cart
from payments.models import Payment, sync_order_ledger, sync_order_settlements
from .models import Order, OrderItem


def customer_required(view_func):
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('accounts:login')
        allowed = request.user.get_roles_list()
        if 'customer' not in allowed:
            return redirect('accounts:login')
        request.session['active_role'] = 'customer'
        return view_func(request, *args, **kwargs)
    wrapper.__name__ = view_func.__name__
    return wrapper


@customer_required
def checkout(request):
    from products.views import get_distributor

    distributor = get_distributor(request)
    if not distributor:
        messages.error(request, 'You need a distributor to place orders. Please register with a distributor invite code.')
        return redirect('products:home')
    cart_items = Cart.objects.filter(customer=request.user).select_related(
        'variant__product__supplier'
    ).prefetch_related(
        Prefetch(
            'variant__dist_prices',
            queryset=__import__('distributors.models', fromlist=['DistributorVariantPrice'])
                     .DistributorVariantPrice.objects.filter(distributor=distributor, is_active=True),
            to_attr='_prefetched_dist_prices',
        )
    )
    if not cart_items.exists():
        messages.warning(request, 'Your cart is empty.')
        return redirect('cart:cart')

    # Calculate totals using distributor prices
    # Pre-compute per-item subtotals for template display (templates can't pass args)
    for item in cart_items:
        item.display_subtotal = item.subtotal(distributor)
    subtotal = sum(item.display_subtotal for item in cart_items)

    # Delivery charge — use customer's zone if assigned, else distributor default
    delivery_charge = distributor.delivery_charge if distributor else 0
    free_delivery_above = distributor.free_delivery_above if distributor else 0
    delivery_zone_name = ''

    if distributor and request.user.is_authenticated:
        from distributors.models import CustomerZone
        cz = CustomerZone.objects.filter(
            customer=request.user, distributor=distributor
        ).select_related('zone').first()
        if cz and cz.zone and cz.zone.is_active:
            delivery_charge = cz.zone.delivery_charge
            free_delivery_above = cz.zone.free_delivery_above
            delivery_zone_name = cz.zone.name

    if free_delivery_above and subtotal >= free_delivery_above:
        delivery_charge = 0

    total = subtotal + delivery_charge

    # Saved addresses
    from accounts.models import CustomerAddress
    saved_addresses = CustomerAddress.objects.filter(user=request.user)

    if request.method == 'POST':
        address_id = request.POST.get('address_id', '').strip()
        address = request.POST.get('address', '').strip()
        phone = request.POST.get('phone', '').strip()
        # COD is the only supported payment method. Ignore any other posted value.
        payment_method = 'cod'

        if address_id:
            addr_obj = get_object_or_404(CustomerAddress, pk=address_id, user=request.user)
            address = addr_obj.get_full_address()
            if not phone:
                phone = addr_obj.phone
        else:
            door_number = request.POST.get('door_number', '').strip()
            street_details = request.POST.get('street_details', '').strip()
            locality = request.POST.get('locality', '').strip()
            city = request.POST.get('city', '').strip()
            pincode = request.POST.get('pincode', '').strip()
            
            if door_number and street_details and locality and city and pincode:
                # Save as new address
                addr_obj = CustomerAddress.objects.create(
                    user=request.user,
                    label=request.POST.get('label', 'Home'),
                    name=request.POST.get('name', request.user.get_full_name() or request.user.username),
                    phone=phone,
                    door_number=door_number,
                    street_details=street_details,
                    locality=locality,
                    city=city,
                    pincode=pincode,
                    google_map_link=request.POST.get('google_map_link', ''),
                    is_default=True
                )
                address = addr_obj.get_full_address()
        if not address or not phone:
            messages.error(request, 'Address and phone are required.')
            return render(request, 'orders/checkout.html', {
                'cart_items': cart_items, 'subtotal': subtotal,
                'delivery_charge': delivery_charge, 'total': total,
                'distributor': distributor,
                'saved_addresses': saved_addresses,
            })


        try:
            with transaction.atomic():
                # ── 1. Reward: AUTO-redeem up to the shop's max % (default 50%) ──
                from distributors.rewards import redeem_points as do_redeem, get_redeemable_info
                reward_discount = 0
                r_info = get_redeemable_info(request.user, distributor, subtotal) if distributor else None
                if r_info and r_info.get('can_redeem') and r_info.get('max_points'):
                    reward_discount = do_redeem(request.user, distributor, r_info['max_points'])
                    total = max(0, total - reward_discount)

                # ── 2. Wallet: pay from the customer's wallet if they chose to ──
                from rentals.services import wallet_balance, debit_wallet
                wallet_paid = 0
                if request.POST.get('use_wallet') == '1':
                    wallet_paid = min(wallet_balance(request.user, distributor), total)
                    if wallet_paid > 0:
                        debit_wallet(request.user, distributor, wallet_paid, 'shop_pay',
                                     note='Order payment', by=request.user.username)
                        total = total - wallet_paid

                # ── 3. Create order header (inside atomic — rolled back on any failure) ──
                order = Order.objects.create(
                    customer=request.user,
                    distributor=distributor,
                    subtotal=subtotal,
                    delivery_charge=delivery_charge,
                    total_amount=total,
                    reward_discount=reward_discount,
                    wallet_paid=wallet_paid,
                    address=address,
                    phone=phone,
                )

                # ── 3. Check & lock distributor stock (inside atomic — select_for_update valid) ──
                from distributors.models import DistributorStock, DistributorLooseStock

                stock_map = {
                    s.variant_id: s
                    for s in DistributorStock.objects.select_for_update().filter(
                        distributor=distributor
                    )
                }
                loose_map = {
                    ls.product_id: ls
                    for ls in DistributorLooseStock.objects.select_for_update().filter(
                        distributor=distributor
                    )
                }

                def available_qty(item):
                    """Total available from packed stock + loose stock."""
                    packed = stock_map.get(item.variant_id)
                    packed_qty = packed.quantity if packed else 0
                    loose = loose_map.get(item.variant.product_id)
                    loose_qty = loose.available_units(item.variant) if loose else 0
                    return packed_qty + loose_qty

                all_from_dist_stock = all(
                    available_qty(item) >= item.quantity for item in cart_items
                )
                any_from_dist_stock = any(
                    available_qty(item) > 0 for item in cart_items
                )

                def consume_item(item, qty):
                    """Consume qty from packed first, then loose."""
                    from distributors.models import consume_stock_fifo
                    packed = stock_map.get(item.variant_id)
                    packed_qty = packed.quantity if packed else 0
                    from_packed = min(packed_qty, qty)
                    from_loose = qty - from_packed
                    if from_packed > 0:
                        try:
                            consume_stock_fifo(distributor, item.variant, from_packed)
                        except ValueError:
                            pass
                    if from_loose > 0:
                        loose = loose_map.get(item.variant.product_id)
                        if loose:
                            loose.deduct(item.variant, from_loose)

                if all_from_dist_stock:
                    order.fulfillment_source = 'distributor_stock'
                    order.status = 'at_distributor'
                    order.save()
                    for item in cart_items:
                        consume_item(item, item.quantity)
                    # Stage 7g: the sale just lowered shop stock — top up if the
                    # shop opted into auto-restock (runs after commit).
                    from django.db import transaction as _tx
                    from stockist.auto_restock import check_marketplace
                    _tx.on_commit(lambda d=distributor: check_marketplace(d))
                elif distributor.allow_partial_shipment and any_from_dist_stock:
                    order.fulfillment_source = 'distributor_stock'
                    order.status = 'at_distributor'
                    order.is_partial_shipment = True
                    order.save()
                    for item in cart_items:
                        avail = available_qty(item)
                        if avail >= item.quantity:
                            consume_item(item, item.quantity)
                            item._fulfillment_status = 'shipped'
                        elif avail > 0:
                            consume_item(item, avail)
                            item._fulfillment_status = 'shipped'
                        else:
                            item._fulfillment_status = 'backordered'
                    # Stage 7g: partial shipment consumed shop stock too.
                    from django.db import transaction as _tx
                    from stockist.auto_restock import check_marketplace
                    _tx.on_commit(lambda d=distributor: check_marketplace(d))
                else:
                    order.fulfillment_source = 'supplier'
                    order.save()

                # ── 4. Create order items & deduct product stock (locked rows) ──
                from stockist.models import get_stockist_price, stockist_for
                active_stockist = stockist_for(distributor)

                for item in cart_items:
                    from products.models import ProductVariant, Product
                    variant = ProductVariant.objects.select_for_update().get(pk=item.variant_id)
                    product = variant.product
                    supplier = product.supplier

                    unit_price = item.unit_price(distributor)
                    base_price = variant.selling_price
                    if active_stockist:
                        current_stockist_price, _ = get_stockist_price(product, variant, active_stockist, enterprise=distributor)
                        base_price = max(base_price, current_stockist_price)
                    if distributor:
                        dvp = variant.dist_prices.filter(distributor=distributor, is_active=True).first()
                        if dvp:
                            base_price = max(base_price, dvp.base_price)

                    if variant.stock < item.quantity:
                        raise ValueError(
                            f'Insufficient stock for {variant}. '
                            f'Available: {variant.stock}, requested: {item.quantity}'
                        )

                    item_fulfillment = getattr(item, '_fulfillment_status', 'pending')

                    resolved_mrp = variant.mrp
                    if resolved_mrp is None:
                        resolved_mrp = max(unit_price, base_price, variant.selling_price)

                    OrderItem.objects.create(
                        order=order,
                        product=product,
                        variant=variant,
                        variant_label=variant.label,
                        quantity=item.quantity,
                        price=unit_price,
                        base_price=base_price,
                        supplier_price=variant.selling_price,
                        mrp=resolved_mrp,
                        fulfillment_status=item_fulfillment,
                    )

                    variant.stock = variant.stock - item.quantity
                    variant.save(update_fields=['stock'])

                    if variant.unit_grams and product.master_stock_grams:
                        product = Product.objects.select_for_update().get(pk=product.pk)
                        grams_used = item.quantity * variant.unit_grams
                        product.master_stock_grams = max(0, product.master_stock_grams - grams_used)
                        product.save(update_fields=['master_stock_grams'])
                        product.sync_variant_stocks()

                # ── 5. Payment, ledger, reward link, cart clear (all inside atomic) ──
                # COD is always pending until the money is collected on delivery.
                Payment.objects.create(
                    order=order,
                    amount=total,
                    payment_method='cod',
                    status='pending',
                )
                sync_order_ledger(order)
                sync_order_settlements(order)
                # ── Activity log: order placed ──
                try:
                    from lakrish_mart.activity_logger import act as _act
                    _act.order('placed', order=order, by=request.user,
                               detail=f'items={order.items.count()} source={order.fulfillment_source}')
                except Exception:
                    pass

                if reward_discount > 0:
                    from distributors.reward_models import RewardPoints
                    rp = RewardPoints.objects.filter(customer=request.user, distributor=distributor).first()
                    if rp:
                        tx = rp.transactions.filter(
                            transaction_type='redeemed', order__isnull=True
                        ).order_by('-created_at').first()
                        if tx:
                            tx.order = order
                            tx.save(update_fields=['order'])

                cart_items.delete()

        except ValueError as e:
            messages.error(request, f'Could not place order: {e}. Please review your cart and try again.')
            return redirect('cart:cart')

        messages.success(request, f'Order #{order.id} placed successfully!')
        return redirect('orders:order_detail', pk=order.id)

    from distributors.rewards import get_redeemable_info
    reward_info = get_redeemable_info(request.user, distributor, subtotal) if distributor else None
    # Reward now AUTO-applies up to the shop's max % — show the amount, not an input.
    reward_auto = reward_info['max_points'] if (reward_info and reward_info.get('can_redeem')) else 0
    from rentals.services import wallet_balance as _wallet_balance
    wallet_bal = _wallet_balance(request.user, distributor) if distributor else 0
    after_reward = max(0, total - reward_auto)

    return render(request, 'orders/checkout.html', {
        'cart_items': cart_items,
        'subtotal': subtotal,
        'delivery_charge': delivery_charge,
        'total': total,
        'reward_auto': reward_auto,
        'wallet_balance': wallet_bal,
        'wallet_use': min(wallet_bal, after_reward),
        'after_reward': after_reward,
        'distributor': distributor,
        'saved_addresses': saved_addresses,
        'reward_info': reward_info,
        'delivery_zone_name': delivery_zone_name,
    })


@customer_required
def order_list(request):
    from django.core.paginator import Paginator
    from django.conf import settings
    orders = Order.objects.filter(customer=request.user).order_by('-created_at')
    paginator = Paginator(orders, getattr(settings, 'ORDERS_PER_PAGE', 20))
    page = paginator.get_page(request.GET.get('page'))

    # Reward points balance
    reward_balance = None
    from products.views import get_distributor
    distributor = get_distributor(request)
    if distributor:
        try:
            from distributors.reward_models import RewardPoints
            reward_balance = RewardPoints.objects.filter(
                customer=request.user, distributor=distributor
            ).first()
        except Exception:
            pass

    return render(request, 'orders/order_list.html', {
        'orders': page, 'page_obj': page,
        'reward_balance': reward_balance,
    })


@customer_required
def order_detail(request, pk):
    from django.db.models import Prefetch
    from orders.models import OrderItem
    order = get_object_or_404(
        Order.objects.prefetch_related(Prefetch(
            'items',
            queryset=OrderItem.objects.select_related('product', 'variant', 'supplier_shipment'),
        )),
        pk=pk, customer=request.user,
    )
    # The supplier's stage (packed / delayed / unavailable ...) only matters
    # while the goods are still with the supplier. Express orders served from
    # the shop's own stock never involve the supplier at all.
    show_supplier_stage = (
        order.fulfillment_source == 'supplier' and order.status in ('pending', 'confirmed')
    )
    return render(request, 'orders/order_detail.html', {
        'order': order,
        'show_supplier_stage': show_supplier_stage,
    })


@customer_required
def reorder(request, pk):
    """Re-add all items from a past order to cart."""
    from cart.models import Cart
    order = get_object_or_404(Order, pk=pk, customer=request.user)
    added = 0
    for item in order.items.all():
        if item.variant and item.variant.is_in_stock():
            Cart.objects.update_or_create(
                customer=request.user, variant=item.variant,
                defaults={'quantity': item.quantity}
            )
            added += 1
    if added:
        messages.success(request, f'{added} item(s) added to cart.')
    else:
        messages.warning(request, 'No items could be added (out of stock).')
    return redirect('cart:cart')



@customer_required
def order_edit(request, pk):
    """Customer can edit a pending order — change qty, remove items, change address."""
    order = get_object_or_404(Order, pk=pk, customer=request.user)
    if order.status != 'pending':
        messages.error(request, 'This order can no longer be edited.')
        return redirect('orders:order_detail', pk=pk)

    from accounts.models import CustomerAddress
    saved_addresses = CustomerAddress.objects.filter(user=request.user)
    selected_address_id = None
    matched_addr = saved_addresses.filter(phone=order.phone).first()
    if matched_addr and matched_addr.get_full_address() == order.address:
        selected_address_id = matched_addr.pk
    elif saved_addresses:
        selected_address_id = saved_addresses.first().pk

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'update_address':
            address_id = request.POST.get('address_id')
            if address_id:
                addr = get_object_or_404(CustomerAddress, pk=address_id, user=request.user)
                order.address = addr.get_full_address()
                order.phone = addr.phone
            else:
                order.address = request.POST.get('address', order.address).strip()
                order.phone = request.POST.get('phone', order.phone).strip()
            order.save(update_fields=['address', 'phone'])

        elif action == 'update_qty':
            item_id = request.POST.get('item_id')
            qty = int(request.POST.get('quantity', 1))
            item = get_object_or_404(OrderItem, pk=item_id, order=order)
            if qty < 1:
                item.delete()
                messages.success(request, 'Item removed.')
            else:
                item.quantity = qty
                item.save(update_fields=['quantity'])
                messages.success(request, 'Quantity updated.')
            _recalculate_order(order)

        elif action == 'remove_item':
            item_id = request.POST.get('item_id')
            item = get_object_or_404(OrderItem, pk=item_id, order=order)
            item.delete()
            messages.success(request, 'Item removed.')
            _recalculate_order(order)
            if not order.items.exists():
                order.status = 'cancelled'
                order.save(update_fields=['status'])
                from distributors.rewards import reverse_order_rewards
                reverse_order_rewards(order)
                _sync_payment_and_ledger(order)
                messages.warning(request, 'Order cancelled — no items remaining.')
                return redirect('orders:order_list')

        return redirect('orders:order_edit', pk=pk)

    return render(request, 'orders/order_edit.html', {
        'order': order,
        'saved_addresses': saved_addresses,
        'selected_address_id': selected_address_id,
    })


def _recalculate_order(order):
    """Recalculate order subtotal and total after item changes."""
    from decimal import Decimal

    subtotal = sum(item.price * item.quantity for item in order.items.all())
    reward_discount = _reward_discount_for_order(order)
    order.subtotal = subtotal
    order.total_amount = max(Decimal('0'), subtotal + order.delivery_charge - reward_discount)
    order.save(update_fields=['subtotal', 'total_amount'])
    _sync_payment_and_ledger(order)


def _reward_discount_for_order(order):
    redeemed_total = order.reward_transactions.filter(
        transaction_type='redeemed'
    ).aggregate(total=models.Sum('points'))['total'] or 0
    return abs(redeemed_total)


def _sync_payment_and_ledger(order):
    payment = getattr(order, 'payment', None)
    if payment:
        payment.amount = order.total_amount
        if order.status == 'cancelled':
            payment.status = 'refunded' if payment.status == 'paid' else 'failed'
        payment.save(update_fields=['amount', 'status'])
    sync_order_ledger(order)
    sync_order_settlements(order)
