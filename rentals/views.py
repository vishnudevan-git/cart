"""
Rent views — grouped by who uses them:

  admin_*        the ONE master switch + read-only oversight
  shop_*         the marketplace: its items, the approval queue, bookings, wallet top-ups
  customer_*     browse, book (from the wallet), my rentals, publish an item
  dist_*         the wholesale Distributor: rent from its shops (offline) & publish into them

New-rental surfaces (browse / item / book / publish / wallet-load / block-days)
are behind the master switch via @rentals_on. The four booking-management pages
are deliberately NOT gated, so an in-flight booking can always be confirmed,
or cancelled (and refunded) even after the admin turns rentals off —
turning off stops NEW rentals without stranding open ones. The admin switch page
is ungated so the admin can reach it to turn the feature back on.

Money moves and booking transitions all go through rentals.services — no view
hand-rolls them.
"""
from decimal import Decimal

from django.contrib import messages
from django.db.models import Sum
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import role_required
from accounts.utils import parse_decimal as _money, parse_int as _int, parse_day as _parse_day
from admin_panel.models import SiteSettings

from .decorators import rentals_on
from .models import Wallet, RentalItem, RentalBlock, RentalBooking
from . import services

admin_required = role_required('admin', set_active_role='admin', allow_superuser=True)
shop_required = role_required('distributor', set_active_role='distributor')
customer_required = role_required('customer', set_active_role='customer')
wholesaler_required = role_required('wholesaler', set_active_role='wholesaler')


# ── Shared POST helpers ──────────────────────────────────────────────────────
def _save_item_from_post(request, distributor, owner_kind, status, existing=None):
    """Create or update a rental item from a POST. Returns the saved item, or
    None after flashing the validation error. One home for the three publish
    forms (shop / customer / distributor)."""
    name = (request.POST.get('name') or '').strip()
    rent = _money(request.POST.get('rent_per_day'))
    cost = _money(request.POST.get('item_cost'))
    if not name or rent <= 0 or cost <= 0:
        messages.error(request, 'Name, a rent/day above ₹0 and an item cost above ₹0 are required.')
        return None
    item = existing or RentalItem(distributor=distributor, owner=request.user, owner_kind=owner_kind)
    item.name = name
    item.description = (request.POST.get('description') or '').strip()
    item.category = request.POST.get('category', 'other')
    item.rent_per_day = rent
    item.item_cost = cost
    item.units = max(1, _int(request.POST.get('units'), 1))
    item.status = status
    if request.FILES.get('image'):
        item.image = request.FILES['image']
    item.save()
    return item


def _place_booking(request, item, renter_kind, back_to):
    """Shared renter booking POST (customer pays wallet, Distributor offline).
    Always returns a redirect response."""
    start = _parse_day(request.POST.get('start_day'))
    days = _int(request.POST.get('days'), 0)
    if not start or start < timezone.localdate():
        messages.error(request, 'Pick a start day from today onward.')
    elif days < 1:
        messages.error(request, 'Rent for at least one day.')
    else:
        try:
            booking = services.create_booking(item, request.user, renter_kind, start, days,
                                              phone=request.user.phone or '')
            if renter_kind == 'customer':
                messages.success(request, f'Booked {item.name} for {days} day(s) — ₹{booking.amount} '
                                          f'paid from your wallet. The owner will confirm and call you.')
                return redirect('rentals:customer_bookings')
            messages.success(request, f'Requested {item.name} for {days} day(s). '
                                      f'Pay the owner offline — the amount is recorded.')
            return redirect('rentals:dist_bookings')
        except ValueError as exc:
            messages.error(request, str(exc))
    return redirect(back_to, pk=item.pk)


def _advance_booking(request, booking):
    """Owner-side lifecycle action (confirm or reject). Cancel is a renter
    action and is ignored here."""
    action = request.POST.get('action')
    if action == 'cancel':
        return
    level, msg = services.transition_booking(booking, action, by=request.user.username)
    getattr(messages, level)(request, msg)


# ── Admin: the master switch + oversight ─────────────────────────────────────
@admin_required
def admin_rentals(request):
    """The single global control. Also a read-only overview once it's on."""
    site = SiteSettings.get()
    active_qs = RentalBooking.objects.filter(status__in=RentalBooking.ACTIVE_STATUSES)
    if request.method == 'POST':
        site.rentals_enabled = request.POST.get('rentals_enabled') == '1'
        site.save(update_fields=['rentals_enabled', 'updated_at'])
        if site.rentals_enabled:
            messages.success(request, 'Rentals are now ON everywhere.')
        else:
            # Turning off stops NEW rentals only — in-flight bookings stay
            # manageable, so nothing (and no wallet money) gets stranded.
            active = active_qs.count()
            if active:
                messages.warning(
                    request,
                    f'Rentals are now OFF for new bookings. {active} booking(s) are still in '
                    f'progress — they can still be confirmed or cancelled (with wallet '
                    f'refunds) from the booking pages until they close.')
            else:
                messages.success(request, 'Rentals are now OFF everywhere.')
        return redirect('rentals:admin_rentals')

    ctx = {
        'active_nav': 'rentals',
        'rentals_enabled': site.rentals_enabled,
        'item_count': RentalItem.objects.filter(is_deleted=False).count(),
        'pending_count': RentalItem.objects.filter(status='pending', is_deleted=False).count(),
        'booking_count': RentalBooking.objects.count(),
        'active_count': active_qs.count(),
        'wallet_total': Wallet.objects.aggregate(t=Sum('balance'))['t'] or Decimal('0'),
        'recent_bookings': RentalBooking.objects.select_related(
            'item', 'renter', 'item__distributor')[:20],
    }
    return render(request, 'rentals/admin_rentals.html', ctx)


# ── Marketplace (shop) ───────────────────────────────────────────────────────
@shop_required
@rentals_on
def shop_items(request):
    dist = request.user.distributor_profile
    items = RentalItem.objects.filter(distributor=dist, is_deleted=False).select_related('owner')
    pending = [i for i in items if i.status == 'pending']
    live = [i for i in items if i.status == 'approved']
    hidden = [i for i in items if i.status == 'hidden']
    return render(request, 'rentals/shop_items.html', {
        'active_nav': 'rentals', 'dist': dist,
        'pending': pending, 'live': live, 'hidden': hidden,
    })


@shop_required
@rentals_on
def shop_item_form(request, pk=None):
    dist = request.user.distributor_profile
    item = get_object_or_404(RentalItem, pk=pk, distributor=dist, is_deleted=False) if pk else None
    if request.method == 'POST':
        # The shop's own items go live immediately — no one to approve them.
        saved = _save_item_from_post(request, dist, 'shop', 'approved', existing=item)
        if saved is None:
            return redirect(request.path)
        messages.success(request, f'"{saved.name}" saved and live for rent.')
        return redirect('rentals:shop_items')
    return render(request, 'rentals/shop_item_form.html', {
        'active_nav': 'rentals', 'item': item,
        'categories': RentalItem.CATEGORY_CHOICES,
    })


@shop_required
@rentals_on
@require_POST
def shop_item_moderate(request, pk):
    """Approve / reject / hide / show / remove an item in this shop's rentals."""
    dist = request.user.distributor_profile
    item = get_object_or_404(RentalItem, pk=pk, distributor=dist, is_deleted=False)
    action = request.POST.get('action')
    if action == 'approve':
        item.status = 'approved'; item.save(update_fields=['status'])
        messages.success(request, f'"{item.name}" approved — now bookable.')
    elif action in ('reject', 'hide'):
        # Soft: a rejected submission is hidden, never hard-deleted.
        item.status = 'hidden'; item.save(update_fields=['status'])
        messages.warning(request, f'"{item.name}" is now hidden.')
    elif action == 'show':
        item.status = 'approved'; item.save(update_fields=['status'])
        messages.success(request, f'"{item.name}" visible again.')
    elif action == 'remove':
        item.is_deleted = True; item.deleted_at = timezone.now()
        item.save(update_fields=['is_deleted', 'deleted_at'])
        messages.success(request, f'"{item.name}" removed.')
    return redirect('rentals:shop_items')


@shop_required
# NOT @rentals_on: a close-out page — open bookings must be resolvable even
# after the admin turns rentals off (see admin_rentals warning).
def shop_bookings(request):
    dist = request.user.distributor_profile
    # The shop confirms bookings on its OWN and its Distributor's items; a
    # customer-owner handles bookings on their own items (customer_owned_bookings).
    base = RentalBooking.objects.filter(
        item__distributor=dist, item__owner_kind__in=('shop', 'distributor'))
    services.settle_due(base)          # pay owners / expire stale requests that are due
    if request.method == 'POST':
        booking = get_object_or_404(base, pk=request.POST.get('booking_id'))
        _advance_booking(request, booking)
        return redirect('rentals:shop_bookings')
    return render(request, 'rentals/shop_bookings.html', {
        'active_nav': 'rentals', 'bookings': base.select_related('item', 'renter'),
    })


@shop_required
@rentals_on
def shop_wallets(request):
    """Load a customer's wallet (cash paid to the shop) and see balances."""
    from accounts.models import User
    dist = request.user.distributor_profile
    if request.method == 'POST':
        customer = User.objects.filter(pk=request.POST.get('customer_id'),
                                       distributor=dist, role='customer').first()
        amount = _money(request.POST.get('amount'))
        if not customer or amount <= 0:
            messages.error(request, 'Pick a customer and an amount above ₹0.')
        else:
            services.credit_wallet(customer, dist, amount, 'load',
                                   note='Cash loaded at shop', by=request.user.username)
            messages.success(request, f'₹{amount} loaded to {customer.get_full_name() or customer.username}.')
        return redirect('rentals:shop_wallets')

    customers = User.objects.filter(distributor=dist, role='customer', is_deleted=False).order_by('first_name', 'username')
    wallets = {w.customer_id: w.balance for w in Wallet.objects.filter(distributor=dist)}
    rows = [{'customer': c, 'balance': wallets.get(c.pk, Decimal('0'))} for c in customers]
    return render(request, 'rentals/shop_wallets.html', {'active_nav': 'rentals', 'rows': rows})


@shop_required
@rentals_on
def shop_blocks(request, pk):
    """The shop blocks away-days on one of ITS OWN items."""
    dist = request.user.distributor_profile
    item = get_object_or_404(RentalItem, pk=pk, distributor=dist, owner_kind='shop', is_deleted=False)
    return _handle_blocks(request, item, 'rentals:shop_blocks')


# ── Customer ─────────────────────────────────────────────────────────────────
def _customer_shop(user):
    return getattr(user, 'distributor', None)


@customer_required
@rentals_on
def customer_list(request):
    dist = _customer_shop(request.user)
    if not dist:
        return render(request, 'rentals/customer_list.html', {'no_shop': True, 'items': []})
    items = RentalItem.objects.filter(distributor=dist, status='approved', is_deleted=False)
    return render(request, 'rentals/customer_list.html', {
        'items': items, 'dist': dist,
        'balance': services.wallet_balance(request.user, dist),
    })


@customer_required
@rentals_on
def customer_item(request, pk):
    dist = _customer_shop(request.user)
    item = get_object_or_404(RentalItem, pk=pk, distributor=dist, status='approved', is_deleted=False)
    if request.method == 'POST':
        return _place_booking(request, item, 'customer', 'rentals:customer_item')
    today = timezone.localdate()
    return render(request, 'rentals/customer_item.html', {
        'item': item, 'today': today.isoformat(),
        'balance': services.wallet_balance(request.user, dist),
        'grid': services.day_grid(item, today, 14),
    })


@customer_required
# NOT @rentals_on: close-out page (cancel your open bookings even when off).
def customer_bookings(request):
    dist = _customer_shop(request.user)
    base = RentalBooking.objects.filter(renter=request.user)
    services.settle_due(base)          # surface completions / auto-refunds
    if request.method == 'POST':
        booking = get_object_or_404(base, pk=request.POST.get('booking_id'))
        if request.POST.get('action') == 'cancel':
            level, msg = services.transition_booking(booking, 'cancel', by=request.user.username)
            getattr(messages, level)(request, msg)
        return redirect('rentals:customer_bookings')
    return render(request, 'rentals/customer_bookings.html', {
        'bookings': base.select_related('item'),
        'balance': services.wallet_balance(request.user, dist) if dist else Decimal('0'),
    })


@customer_required
@rentals_on
def customer_publish(request):
    """A customer lists their own item — goes to the shop for approval."""
    dist = _customer_shop(request.user)
    if not dist:
        messages.error(request, 'You are not linked to a shop yet.')
        return redirect('products:home')
    if request.method == 'POST':
        item = _save_item_from_post(request, dist, 'customer', 'pending')
        if item is None:
            return redirect(request.path)
        messages.success(request, f'"{item.name}" sent to {dist.company_name} for approval.')
        return redirect('rentals:customer_bookings')
    return render(request, 'rentals/customer_publish.html', {
        'dist': dist, 'categories': RentalItem.CATEGORY_CHOICES,
    })


@customer_required
@rentals_on
def customer_blocks(request, pk):
    """Owner (a customer) blocks days their own item is unavailable (away)."""
    item = get_object_or_404(RentalItem, pk=pk, owner=request.user, is_deleted=False)
    return _handle_blocks(request, item, 'rentals:customer_blocks')


@customer_required
# NOT @rentals_on: close-out page (resolve bookings on your items when off).
def customer_owned_bookings(request):
    """Bookings ON the items a customer LISTED — they confirm or reject."""
    base = RentalBooking.objects.filter(item__owner=request.user, item__owner_kind='customer')
    services.settle_due(base)
    if request.method == 'POST':
        booking = get_object_or_404(base, pk=request.POST.get('booking_id'))
        _advance_booking(request, booking)
        return redirect('rentals:customer_owned_bookings')
    return render(request, 'rentals/owner_bookings.html', {
        'bookings': base.select_related('item', 'renter'), 'title': 'Bookings on my items',
    })


def _handle_blocks(request, item, redirect_name):
    """Shared owner block/unblock of away-days (customer & shop)."""
    if request.method == 'POST':
        day = _parse_day(request.POST.get('day'))
        if day:
            if request.POST.get('action') == 'unblock':
                RentalBlock.objects.filter(item=item, day=day).delete()
            else:
                RentalBlock.objects.get_or_create(item=item, day=day)
        return redirect(redirect_name, pk=item.pk)
    return render(request, 'rentals/customer_blocks.html', {
        'item': item, 'grid': services.day_grid(item, timezone.localdate(), 21),
        'redirect_name': redirect_name,
    })


# ── Distributor (wholesale) — rents from its shops & publishes into them ──────
def _wholesaler_shops(user):
    from distributors.models import Distributor
    return Distributor.objects.filter(wholesaler=user.wholesaler_profile, status='active')


@wholesaler_required
@rentals_on
def dist_list(request):
    """Browse rental items across all the shops this Distributor supplies."""
    items = RentalItem.objects.filter(
        distributor__in=_wholesaler_shops(request.user), status='approved', is_deleted=False
    ).select_related('distributor')
    return render(request, 'rentals/dist_list.html', {'items': items})


@wholesaler_required
@rentals_on
def dist_item(request, pk):
    item = get_object_or_404(RentalItem, pk=pk, distributor__in=_wholesaler_shops(request.user),
                             status='approved', is_deleted=False)
    if request.method == 'POST':
        return _place_booking(request, item, 'distributor', 'rentals:dist_item')
    today = timezone.localdate()
    return render(request, 'rentals/dist_item.html', {
        'item': item, 'today': today.isoformat(),
        'grid': services.day_grid(item, today, 14),
    })


@wholesaler_required
# NOT @rentals_on: close-out page (cancel your open bookings even when off).
def dist_bookings(request):
    base = RentalBooking.objects.filter(renter=request.user)
    services.settle_due(base)
    if request.method == 'POST':
        booking = get_object_or_404(base, pk=request.POST.get('booking_id'))
        if request.POST.get('action') == 'cancel':
            level, msg = services.transition_booking(booking, 'cancel', by=request.user.username)
            getattr(messages, level)(request, msg)
        return redirect('rentals:dist_bookings')
    return render(request, 'rentals/owner_bookings.html', {
        'bookings': base.select_related('item'), 'title': 'My rental bookings', 'as_renter': True,
    })


@wholesaler_required
@rentals_on
def dist_publish(request):
    """Publish an item into one of this Distributor's shops (needs shop approval)."""
    shops = _wholesaler_shops(request.user)
    if request.method == 'POST':
        shop = shops.filter(pk=request.POST.get('shop_id')).first()
        if not shop:
            messages.error(request, 'Pick one of your shops.')
            return redirect(request.path)
        item = _save_item_from_post(request, shop, 'distributor', 'pending')
        if item is None:
            return redirect(request.path)
        messages.success(request, f'"{item.name}" sent to {shop.company_name} for approval.')
        return redirect('rentals:dist_bookings')
    return render(request, 'rentals/dist_publish.html', {
        'shops': shops, 'categories': RentalItem.CATEGORY_CHOICES,
    })
