import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.utils import timezone
from django.views.decorators.http import require_POST
from orders.models import Order
from .models import Delivery

logger = logging.getLogger(__name__)


def _can_manage_delivery(user, order):
    """Return True if user is allowed to view/update this delivery (admin or the order's distributor)."""
    if not user.is_authenticated:
        return False
    if user.has_role('admin') or user.is_superuser:
        return True
    if user.has_role('distributor'):
        try:
            return order.distributor and order.distributor.user_id == user.pk
        except Exception:
            return False
    return False


@login_required
def delivery_list(request):
    """Show deliveries for the current distributor (or all for admin)."""
    user = request.user
    if user.has_role('admin') or user.is_superuser:
        orders = Order.objects.filter(
            status__in=['out_for_delivery', 'delivered', 'partially_delivered']
        ).select_related('customer', 'distributor', 'delivery').order_by('-updated_at')
    elif user.has_role('distributor'):
        try:
            dist = user.distributor_profile
            orders = Order.objects.filter(
                distributor=dist,
                status__in=['out_for_delivery', 'delivered', 'partially_delivered']
            ).select_related('customer', 'delivery').order_by('-updated_at')
        except Exception:
            orders = Order.objects.none()
    else:
        raise PermissionDenied

    return render(request, 'delivery/delivery_list.html', {'orders': orders})


@login_required
def delivery_detail(request, pk):
    """Show detail of a delivery for a specific order."""
    order = get_object_or_404(Order, pk=pk)
    if not _can_manage_delivery(request.user, order):
        raise PermissionDenied
    delivery = getattr(order, 'delivery', None)
    return render(request, 'delivery/delivery_detail.html', {
        'order': order,
        'delivery': delivery,
    })


@login_required
@require_POST
def mark_out_for_delivery(request, pk):
    """Distributor marks an order as out for delivery."""
    order = get_object_or_404(Order, pk=pk)
    if not _can_manage_delivery(request.user, order):
        raise PermissionDenied

    try:
        order.transition_status('out_for_delivery', updated_by=request.user)
        order.save(update_fields=['status', 'updated_at'])
        Delivery.objects.get_or_create(order=order, defaults={'picked_at': timezone.now()})
        try:
            from lakrish_mart.activity_logger import act as _act
            _act.order('out_for_delivery', order=order, by=request.user)
        except Exception:
            pass
        logger.info('Order #%s marked out_for_delivery by %s', order.pk, request.user.username)
        messages.success(request, f'Order #{order.pk} is now out for delivery.')
    except Exception as exc:
        messages.error(request, str(exc))

    return redirect('delivery:delivery_detail', pk=pk)


@login_required
@require_POST
def mark_delivered(request, pk):
    """Distributor marks an order as delivered."""
    order = get_object_or_404(Order, pk=pk)
    if not _can_manage_delivery(request.user, order):
        raise PermissionDenied

    try:
        order.transition_status('delivered', updated_by=request.user)
        order.save(update_fields=['status', 'updated_at'])
        delivery, _ = Delivery.objects.get_or_create(order=order)
        if not delivery.picked_at:
            delivery.picked_at = timezone.now()
        delivery.delivered_at = timezone.now()
        delivery.notes = request.POST.get('notes', '').strip()
        delivery.save()

        # COD close-out: the cash is collected at delivery, so settle the
        # payment now. (Non-COD legacy orders were already 'paid' at checkout.)
        try:
            pay = getattr(order, 'payment', None)
            if pay and pay.payment_method == 'cod' and pay.status == 'pending':
                pay.status = 'paid'
                pay.paid_at = timezone.now()
                # Set amount_collected to the full order amount on a full delivery
                # so the field stays consistent with partial deliveries.
                from decimal import Decimal as _D
                if not pay.amount_collected or pay.amount_collected < pay.amount:
                    pay.amount_collected = pay.amount
                if not pay.received_note:
                    pay.received_note = f'COD collected on delivery by {request.user.username}'
                pay.save(update_fields=['status', 'paid_at', 'amount_collected', 'received_note'])
                try:
                    from lakrish_mart.activity_logger import act as _act
                    _act.order('delivered', order=order, by=request.user)
                    _act.payment('cod_fully_paid', order=order, amount=pay.amount_collected, by=request.user)
                except Exception:
                    pass
                # Keep tier settlements in sync now that the order is paid.
                try:
                    from payments.models import sync_order_ledger, sync_order_settlements
                    sync_order_ledger(order)
                    sync_order_settlements(order)
                except Exception:
                    pass
        except Exception:
            pass

        # Log to financial audit trail
        try:
            from payments.models import FinancialAuditLog
            FinancialAuditLog.log(
                action='order_status_changed',
                actor=request.user,
                obj=order,
                old_value={'status': 'out_for_delivery'},
                new_value={'status': 'delivered'},
                ip_address=request.META.get('REMOTE_ADDR'),
            )
        except Exception:
            pass

        logger.info('Order #%s marked delivered by %s', order.pk, request.user.username)
        messages.success(request, f'Order #{order.pk} marked as delivered.')
    except Exception as exc:
        messages.error(request, str(exc))

    return redirect('delivery:delivery_detail', pk=pk)


@login_required
@require_POST
def mark_partial_delivery(request, pk):
    """Shop delivers a subset of items and collects the proportionate COD cash.

    POST fields:
      item_ids[]     — OrderItem PKs being delivered in this batch
      notes          — optional delivery notes
    The view:
      1. marks selected items fulfillment_status='delivered'
      2. creates a PaymentInstalment for the collected amount (sum of delivered items)
      3. bumps Payment.amount_collected
      4. sets order to 'partially_delivered' or 'delivered' (if all items done)
      5. flips Payment.status to 'paid' once fully collected
    """
    from decimal import Decimal
    from django.db import transaction as _tx
    from orders.models import OrderItem
    from payments.models import Payment, PaymentInstalment

    order = get_object_or_404(Order, pk=pk)
    if not _can_manage_delivery(request.user, order):
        raise PermissionDenied

    item_ids = request.POST.getlist('item_ids')
    notes = request.POST.get('notes', '').strip()

    if not item_ids:
        messages.warning(request, 'Select at least one item to mark as delivered.')
        return redirect('delivery:delivery_detail', pk=pk)

    try:
        with _tx.atomic():
            # 1. Fetch selected items that aren't already delivered.
            delivered_items = list(OrderItem.objects.filter(
                pk__in=item_ids, order=order
            ).exclude(fulfillment_status='delivered'))

            if not delivered_items:
                messages.warning(request, 'Those items are already delivered.')
                return redirect('delivery:delivery_detail', pk=pk)

            batch_amount = sum(it.price * it.quantity for it in delivered_items)

            # Check if this is the final batch — if so include the delivery
            # charge so the customer's full order total is collected.
            remaining_after = order.items.exclude(
                pk__in=[it.pk for it in delivered_items]
            ).exclude(fulfillment_status='delivered').count()
            if remaining_after == 0 and order.delivery_charge:
                batch_amount += order.delivery_charge

            # 2. Create the instalment record.
            pay, _ = Payment.objects.get_or_create(
                order=order,
                defaults={'amount': order.total_amount, 'payment_method': 'cod', 'status': 'pending'})
            instalment = PaymentInstalment.objects.create(
                payment=pay,
                amount=batch_amount,
                collected_by=request.user.username,
                note=notes or f'Partial delivery: {len(delivered_items)} item(s)'
            )

            # 3. Link items to the instalment and mark them delivered.
            for it in delivered_items:
                it.fulfillment_status = 'delivered'
                it.delivery_instalment = instalment
                it.save(update_fields=['fulfillment_status', 'delivery_instalment'])

            # 4. Bump running total on the Payment row.
            pay.amount_collected = Decimal(str(pay.amount_collected or 0)) + batch_amount
            if pay.is_fully_collected:
                pay.status = 'paid'
                pay.paid_at = timezone.now()
                pay.received_note = f'COD fully collected by {request.user.username}'
            else:
                pay.received_note = (f'Partial COD by {request.user.username}: '
                                     f'Rs.{pay.amount_collected} of Rs.{pay.amount} collected')
            pay.save(update_fields=['amount_collected', 'status', 'paid_at', 'received_note'])

            # 5. Update delivery record.
            delivery_obj, _ = Delivery.objects.get_or_create(order=order)
            if not delivery_obj.picked_at:
                delivery_obj.picked_at = timezone.now()
            if notes:
                delivery_obj.notes = notes
            delivery_obj.save(update_fields=['picked_at', 'notes'])

            # 6. Set order status: 'delivered' if all items done, else 'partially_delivered'.
            all_items = list(order.items.all())
            all_delivered = all(it.fulfillment_status == 'delivered' for it in all_items)
            if all_delivered:
                order.status = 'delivered'
                order.is_partial_shipment = False
                delivery_obj.delivered_at = timezone.now()
                delivery_obj.save(update_fields=['delivered_at'])
            else:
                order.status = 'partially_delivered'
                order.is_partial_shipment = True
            order.save(update_fields=['status', 'is_partial_shipment', 'updated_at'])

            # 7. Sync ledger / settlements.
            try:
                from payments.models import sync_order_ledger, sync_order_settlements
                sync_order_ledger(order)
                sync_order_settlements(order)
            except Exception:
                pass

        n = len(delivered_items)
        remaining = order.items.exclude(fulfillment_status='delivered').count()
        try:
            from lakrish_mart.activity_logger import act as _act
            _act.order('partial_delivery' if remaining else 'fully_delivered',
                       order=order, by=request.user,
                       detail=f'batch={n} items batch_amount=Rs.{batch_amount} remaining={remaining}')
            _act.payment('cod_partial_collected' if remaining else 'cod_fully_paid',
                         order=order, amount=batch_amount, by=request.user)
        except Exception:
            pass
        msg = f'{n} item(s) delivered, ₹{batch_amount} collected.'
        if remaining:
            msg += f' {remaining} item(s) still pending — deliver them in the next trip.'
        else:
            msg += ' Order fully delivered and payment complete.'
        messages.success(request, msg)
        logger.info('Order #%s partial delivery by %s: %d items Rs.%s',
                    order.pk, request.user.username, n, batch_amount)

    except Exception as exc:
        messages.error(request, f'Could not process partial delivery: {exc}')

    return redirect('delivery:delivery_detail', pk=pk)
