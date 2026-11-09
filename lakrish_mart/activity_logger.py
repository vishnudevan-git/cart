"""
Central activity logger — imported by views/signals to record every
meaningful business event to logs/activity.log.

Usage:
    from lakrish_mart.activity_logger import act
    act.order("order_confirmed", order=order, by=request.user)
    act.user("registered", user=user, detail="via invite LAA43JSG")
    act.product("approved", product=product, by=request.user)
    act.payment("cod_collected", order=order, amount=Decimal("266"))
    act.stock("reduced", variant=v, qty=-2, by=request.user)
    act.info("custom message here")
    act.error("something went wrong", exc=e)
"""
import logging

_log = logging.getLogger('activity')


def _who(user_or_str):
    if user_or_str is None:
        return 'system'
    if isinstance(user_or_str, str):
        return user_or_str
    try:
        role = getattr(user_or_str, 'role', '')
        return f'{user_or_str.get_username()}({role})'
    except Exception:
        return str(user_or_str)


class _ActivityLogger:

    # ── Order events ──────────────────────────────────────────────────────
    def order(self, event: str, *, order, by=None, detail=''):
        shop = getattr(order.distributor, 'company_name', '–') if order.distributor else '–'
        cust = getattr(order.customer, 'username', '–') if order.customer else '–'
        msg = (f'ORDER | {event.upper():25} | #{order.id} | '
               f'status={order.status} | total=Rs.{order.total_amount} | '
               f'shop={shop} | cust={cust} | by={_who(by)}')
        if detail:
            msg += f' | {detail}'
        _log.info(msg)

    # ── Payment events ────────────────────────────────────────────────────
    def payment(self, event: str, *, order, amount=None, by=None, detail=''):
        pay = getattr(order, 'payment', None)
        status = pay.status if pay else '–'
        collected = pay.amount_collected if pay else 0
        total = pay.amount if pay else 0
        msg = (f'PAYMENT | {event.upper():20} | order=#{order.id} | '
               f'pay_status={status} | collected=Rs.{collected}/Rs.{total}')
        if amount is not None:
            msg += f' | this_batch=Rs.{amount}'
        msg += f' | by={_who(by)}'
        if detail:
            msg += f' | {detail}'
        _log.info(msg)

    # ── User / account events ─────────────────────────────────────────────
    def user(self, event: str, *, user, by=None, detail=''):
        role = getattr(user, 'role', '?')
        shop = (user.distributor.company_name
                if getattr(user, 'distributor', None) else '–')
        msg = (f'USER | {event.upper():20} | {user.get_username()} | '
               f'role={role} | shop={shop} | by={_who(by or user)}')
        if detail:
            msg += f' | {detail}'
        _log.info(msg)

    # ── Product events ────────────────────────────────────────────────────
    def product(self, event: str, *, product, by=None, detail=''):
        cat = product.category.name if product.category else '–'
        msg = (f'PRODUCT | {event.upper():20} | #{product.id} "{product.name}" | '
               f'status={product.status} | cat={cat} | '
               f'sup={product.supplier.company_name} | by={_who(by)}')
        if detail:
            msg += f' | {detail}'
        _log.info(msg)

    # ── Stock events ──────────────────────────────────────────────────────
    def stock(self, event: str, *, variant=None, product=None, qty, by=None, detail=''):
        if variant:
            name = f'{variant.product.name} [{variant.label}]'
            new_stock = variant.stock
        elif product:
            name = product.name
            new_stock = '?'
        else:
            name = '?'; new_stock = '?'
        msg = (f'STOCK | {event.upper():20} | {name} | '
               f'qty_change={qty:+} | stock_now={new_stock} | by={_who(by)}')
        if detail:
            msg += f' | {detail}'
        _log.info(msg)

    # ── Price events ──────────────────────────────────────────────────────
    def price(self, event: str, *, product_name='', variant_label='',
              shop='', old=None, new=None, floor=None, by=None):
        msg = (f'PRICE | {event.upper():20} | {product_name} [{variant_label}] | '
               f'shop={shop} | floor=Rs.{floor} | '
               f'old=Rs.{old} -> new=Rs.{new} | by={_who(by)}')
        _log.info(msg)

    # ── Cart events ───────────────────────────────────────────────────────
    def cart(self, event: str, *, user, variant=None, qty=None, detail=''):
        v = f'{variant.product.name} [{variant.label}]' if variant else '–'
        msg = f'CART | {event.upper():20} | {_who(user)} | {v}'
        if qty is not None:
            msg += f' qty={qty}'
        if detail:
            msg += f' | {detail}'
        _log.info(msg)

    # ── Login / auth events ───────────────────────────────────────────────
    def auth(self, event: str, *, username, role='', ip='', detail=''):
        msg = f'AUTH | {event.upper():20} | {username} | role={role} | ip={ip}'
        if detail:
            msg += f' | {detail}'
        _log.info(msg)

    # ── Generic ───────────────────────────────────────────────────────────
    def info(self, msg: str):
        _log.info(f'INFO  | {msg}')

    def warn(self, msg: str):
        _log.warning(f'WARN  | {msg}')

    def error(self, msg: str, exc=None):
        _log.error(f'ERROR | {msg}' + (f' | {exc}' if exc else ''))


act = _ActivityLogger()
