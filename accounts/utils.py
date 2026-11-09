import re


def normalize_phone(value):
    """Strip +91 or 91 prefix, return bare 10 digits or original string."""
    if re.match(r'^\+91[0-9]{10}$', value):
        return value[3:]
    if re.match(r'^91[0-9]{10}$', value):
        return value[2:]
    return value


def is_valid_phone(value):
    """
    Valid formats:
      - 10 digits              (9876543210)
      - 12 digits starting 91  (919876543210)
      - 13 chars starting +91  (+919876543210)
    """
    if re.match(r'^\+91[0-9]{10}$', value):
        return True
    if re.match(r'^91[0-9]{10}$', value):
        return True
    if re.match(r'^[0-9]{10}$', value):
        return True
    return False


def is_valid_login_identifier(value):
    """Login only accepts 10-digit mobile or valid email."""
    if not value:
        return False
    if value.lower() == 'admin':
        return True
    if re.match(r'^[0-9]{10}$', value):
        return True
    return is_valid_email(value)


def is_valid_email(value):
    return bool(re.match(r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$', value))


def is_valid_username(username):
    """Registration accepts 10-digit, 91+10, +91+10, or email."""
    if not username:
        return False
    if username.lower() == 'admin':
        return True
    return is_valid_phone(username) or is_valid_email(username)


# ── Small shared parsers (form input → Decimal / int / date) ─────────────────
def parse_decimal(raw, default='0'):
    """Lenient money/number parse: trimmed string → Decimal, default on junk."""
    from decimal import Decimal, InvalidOperation
    try:
        return Decimal(str(raw).strip() or default)
    except (InvalidOperation, AttributeError):
        return Decimal(default)


def parse_int(raw, default=0):
    try:
        return int(str(raw).strip() or default)
    except (TypeError, ValueError):
        return default


def parse_day(raw):
    """'YYYY-MM-DD' → date, or None on junk."""
    from datetime import datetime
    try:
        return datetime.strptime((raw or '').strip(), '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None
