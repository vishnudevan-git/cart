"""Human-readable quantities.

Stock is stored in the smallest unit (grams / millilitres / pieces), which is
hard to read at scale (200000 g). These filters show it the way people say it
(200 kg) without changing what is stored.

    {% load qty_tags %}
    {{ product.master_stock_grams|human_qty:product.master_unit }}   ->  200 kg
    {{ product.master_stock_grams|exact_qty:product.master_unit }}   ->  200,000 g
"""
from django import template

register = template.Library()

_LIQUID = ('ml', 'litre', 'l')
_PIECES = ('qty', 'pcs', 'pc')


def _indian_group(int_str):
    """Group an integer digit string Indian-style: 762336 -> '7,62,336'.

    The last 3 digits are one group, then every 2 digits before that.
    """
    neg = int_str.startswith('-')
    if neg:
        int_str = int_str[1:]
    if len(int_str) <= 3:
        return ('-' + int_str) if neg else int_str
    head, last3 = int_str[:-3], int_str[-3:]
    # Split the head into 2-digit groups from the right.
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    parts.insert(0, head)
    grouped = ','.join(parts) + ',' + last3
    return ('-' + grouped) if neg else grouped


def _trim(x, places=2):
    """Indian-grouped, trailing-zeros trimmed: 762336 -> '7,62,336'; 1234.50 -> '1,234.5'."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return x
    s = f'{x:.{places}f}'
    if '.' in s:
        s = s.rstrip('0').rstrip('.')
    if '.' in s:
        int_part, frac = s.split('.', 1)
        return _indian_group(int_part) + '.' + frac
    return _indian_group(s)


@register.filter
def inr(value):
    """Format a number with Indian digit grouping (lakh/crore): 762336 -> '7,62,336'.

    Drop-in replacement for humanize.intcomma on rupee amounts. Keeps up to 2
    decimals, trimming trailing zeros (so 310.00 -> '310', 99.50 -> '99.5').
    """
    return _trim(value, 2)


@register.filter
def human_qty(value, unit='g'):
    """Base-unit amount -> short readable text: 200000 g -> '200 kg', 1500 -> '1.5 kg', 500 -> '500 g'."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return value
    unit = (unit or 'g').lower()
    if unit in _PIECES:
        return f'{_trim(n, 0)} pcs'
    big, small = ('L', 'ml') if unit in _LIQUID else ('kg', 'g')
    if abs(n) >= 1000:
        return f'{_trim(n / 1000)} {big}'
    return f'{_trim(n, 0)} {small}'


@register.filter
def exact_qty(value, unit='g'):
    """The exact stored amount with thousands separators: 200000 -> '200,000 g' (for tooltips)."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return value
    unit = (unit or 'g').lower()
    label = 'pcs' if unit in _PIECES else ('ml' if unit in _LIQUID else 'g')
    return f'{_trim(n, 0)} {label}'
