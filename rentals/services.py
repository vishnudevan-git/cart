"""
Rent business logic — the ONE place money moves, availability is decided, and
booking lifecycle transitions happen.

Everything routes through here so the rules stay honest:
  * the master switch (rentals_enabled) is the single gate,
  * the wallet is real money, ring-fenced from reward points,
  * a unit is not double-booked (best-effort: re-checked inside the booking
    transaction — see create_booking),
  * the renter — never the shop — carries damage/loss.
"""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .models import Wallet, WalletTransaction, RentalBooking, RentalBlock


# ── Master switch ────────────────────────────────────────────────────────────
def rentals_enabled():
    """True only when the admin has switched the Rent feature on."""
    from admin_panel.models import SiteSettings
    return SiteSettings.get().rentals_enabled


# ── Wallet (money; never reward points) ──────────────────────────────────────
def wallet_balance(customer, distributor):
    if customer is None or distributor is None:
        return Decimal('0')
    w = Wallet.objects.filter(customer=customer, distributor=distributor).first()
    return w.balance if w else Decimal('0')


@transaction.atomic
def credit_wallet(customer, distributor, amount, kind, note='', by='', booking=None):
    """Add money to a wallet (load / earn / refund). Returns the WalletTransaction."""
    amount = Decimal(str(amount))
    if amount <= 0:
        raise ValueError('Credit amount must be positive.')
    wallet = Wallet.objects.select_for_update().get_or_create(
        customer=customer, distributor=distributor
    )[0]
    wallet.balance += amount
    wallet.save(update_fields=['balance', 'updated_at'])
    return WalletTransaction.objects.create(
        wallet=wallet, kind=kind, amount=amount, note=note, created_by=by, booking=booking
    )


@transaction.atomic
def debit_wallet(customer, distributor, amount, kind, note='', by='', booking=None):
    """Take money from a wallet (rent payment / damage). Raises if short."""
    amount = Decimal(str(amount))
    if amount <= 0:
        raise ValueError('Debit amount must be positive.')
    wallet = Wallet.objects.select_for_update().get_or_create(
        customer=customer, distributor=distributor
    )[0]
    if wallet.balance < amount:
        raise ValueError(
            f'Not enough wallet balance: have ₹{wallet.balance}, need ₹{amount}.'
        )
    wallet.balance -= amount
    wallet.save(update_fields=['balance', 'updated_at'])
    return WalletTransaction.objects.create(
        wallet=wallet, kind=kind, amount=-amount, note=note, created_by=by, booking=booking
    )


# ── Availability ─────────────────────────────────────────────────────────────
def blocked_days(item):
    return set(RentalBlock.objects.filter(item=item).values_list('day', flat=True))


def _committed_counts(item, exclude_booking_id=None):
    """{day: units committed} across all still-active bookings of this item.

    Reads only (start_day, days) — no need to build full booking objects.
    """
    qs = RentalBooking.objects.filter(item=item, status__in=RentalBooking.ACTIVE_STATUSES)
    if exclude_booking_id:
        qs = qs.exclude(pk=exclude_booking_id)
    counts = {}
    for start, days in qs.values_list('start_day', 'days'):
        for i in range(days):
            d = start + timedelta(days=i)
            counts[d] = counts.get(d, 0) + 1
    return counts


def availability_reason(item, start_day, days):
    """None if the range is bookable, else a human sentence explaining why not."""
    if days < 1:
        return 'Rent for at least one day.'
    wanted = [start_day + timedelta(days=i) for i in range(days)]
    blocks = blocked_days(item)
    committed = _committed_counts(item)
    for d in wanted:
        if d in blocks:
            return f'The owner blocked {d:%d %b} (away) — pick a range that avoids it.'
        if committed.get(d, 0) >= item.units:
            return f'All {item.units} unit(s) are already booked on {d:%d %b}.'
    return None


def day_grid(item, start, span=14):
    """A list of {day, free, blocked} for the next `span` days — for the UI."""
    blocks = blocked_days(item)
    committed = _committed_counts(item)
    grid = []
    for i in range(span):
        d = start + timedelta(days=i)
        grid.append({
            'day': d,
            'blocked': d in blocks,
            'free': max(0, item.units - committed.get(d, 0)),
        })
    return grid


# ── Booking money flow ───────────────────────────────────────────────────────
@transaction.atomic
def create_booking(item, renter, renter_kind, start_day, days, phone=''):
    """Place a booking. Customers pay from the wallet now; businesses settle
    offline. Returns the RentalBooking. Caller must have checked the master
    switch; availability is re-checked here inside the transaction (best-effort
    — not a row-level lock)."""
    reason = availability_reason(item, start_day, days)
    if reason:
        raise ValueError(reason)

    rate = item.rent_per_day
    booking = RentalBooking.objects.create(
        item=item, renter=renter, renter_kind=renter_kind,
        start_day=start_day, days=days, rate_per_day=rate, amount=rate * days,
        renter_phone=phone, status='requested',
    )
    if booking.paid_from_wallet:
        # Pay from the wallet held at this shop. Raises (rolls back) if short.
        debit_wallet(renter, item.distributor, booking.amount, 'rent_pay',
                     note=f'Rent: {item.name} ×{days}d', by=renter.username, booking=booking)
    return booking


@transaction.atomic
def refund_booking(booking, by=''):
    """Give a customer their rent back (reject / cancel before start)."""
    if booking.paid_from_wallet and booking.amount > 0:
        credit_wallet(booking.renter, booking.item.distributor, booking.amount,
                      'refund', note=f'Refund: {booking.item.name}', by=by, booking=booking)


@transaction.atomic
def settle_completion(booking, by=''):
    """The rental period is over — the consumer has used the item, so the money
    held at booking now belongs to the owner. Zero commission: a customer-owner
    is credited the full rent to their wallet; a shop / Distributor owner keeps
    it (revenue / offline). Marks the booking completed."""
    if booking.owner_gets_wallet_credit:
        credit_wallet(booking.item.owner, booking.item.distributor, booking.amount,
                      'rent_earn', note=f'Earned: {booking.item.name} ×{booking.days}d',
                      by=by, booking=booking)
    booking.status = 'completed'
    booking.resolved_at = timezone.now()
    booking.save(update_fields=['status', 'resolved_at'])


# ── Booking lifecycle — the ONE state machine ────────────────────────────────
# Simple flow: book → owner confirm/reject → take away → money auto-transfers to
# the owner after the period ends (settle_due). No handover / return steps.
# action -> (allowed source statuses, target status).
_TRANSITIONS = {
    'confirm': (('requested',),             'confirmed'),
    'reject':  (('requested', 'confirmed'), 'rejected'),
    'cancel':  (('requested', 'confirmed'), 'cancelled'),
}


@transaction.atomic
def transition_booking(booking, action, by=''):
    """Advance a booking. Returns (level, message) to flash; illegal actions
    return ('error', reason) and change nothing. The single home for status
    writes and their money side-effects."""
    spec = _TRANSITIONS.get(action)
    if spec is None or booking.status not in spec[0]:
        return ('error', 'That action is not available for this booking now.')
    _, target = spec

    if action == 'cancel' and booking.start_day <= timezone.localdate():
        return ('error', 'This booking can no longer be cancelled — the rental has started.')

    if action in ('reject', 'cancel'):
        refund_booking(booking, by=by)          # no-op unless paid from wallet

    booking.status = target
    booking.resolved_at = timezone.now()
    booking.save(update_fields=['status', 'resolved_at'])

    if action == 'confirm':
        return ('success', f'Booking #{booking.pk} confirmed — the renter can take it now. '
                           f'Call them on {booking.renter_phone or "—"}. You’ll be paid '
                           f'₹{booking.amount} automatically once the rental period ends.')
    if action == 'reject':
        return ('warning', f'Booking #{booking.pk} rejected — '
                           f'{"wallet refunded." if booking.paid_from_wallet else "no charge."}')
    return ('success', f'Booking #{booking.pk} cancelled'
                       f'{" — wallet refunded." if booking.paid_from_wallet else "."}')


def settle_due(bookings=None):
    """Close bookings whose time is up (safe to run as often as you like):
      * confirmed & rental period over → pay the owner, mark completed;
      * requested & start day passed with no confirm → refund, mark cancelled.
    `end_day` is a derived property, so the date check is done in Python — the
    set of open bookings is always small. Returns (completed, expired)."""
    today = timezone.localdate()
    qs = bookings if bookings is not None else RentalBooking.objects.all()
    completed = expired = 0
    for b in qs.filter(status='confirmed').select_related('item'):
        if b.end_day < today:
            settle_completion(b, by='auto')
            completed += 1
    for b in qs.filter(status='requested').select_related('item'):
        if b.start_day < today:           # owner never confirmed before it started
            refund_booking(b, by='auto')
            b.status = 'cancelled'
            b.resolved_at = timezone.now()
            b.save(update_fields=['status', 'resolved_at'])
            expired += 1
    return (completed, expired)
