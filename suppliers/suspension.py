"""Central partner-suspension logic.

Source of truth for the *rules*: ``kb/08-supplier-suspension.md``. Keep this
module and that document in sync — if you change behaviour here, update the KB
doc, and vice versa.

Suspension is a COMPUTED rule. Nothing in the database is mutated when a
partner is suspended beyond its ``status`` field itself: products and stock are
left intact and simply treated differently while suspended, so reactivation is
instant and needs no repair.

Login block + test-login exclusion apply to EVERY partner tier that has a
suspend status:

    supplier_profile    -> suppliers.Supplier        status 'suspended'
    stockist_profile    -> stockist.Stockist         status 'suspended'
    wholesaler_profile  -> distributors.Wholesaler   status 'suspended'  (UI "Distributor")
    distributor_profile -> distributors.Distributor  status 'suspended'  (UI "Marketplace/Shop")

Customers have no suspend status (they are gated by is_active/is_deleted), so
they are never affected here.

Rules 2 (in-progress-order guard) and 3 (buffer-stock visibility cascade) are
supplier-specific and add their helpers here as they are implemented.
"""
from __future__ import annotations

# Each partner profile and the status value that means "suspended".
# All partner tiers happen to use the literal string 'suspended'.
_PARTNER_PROFILES = (
    'supplier_profile',
    'stockist_profile',
    'wholesaler_profile',
    'distributor_profile',
)


def is_user_suspended_partner(user) -> bool:
    """True when ``user`` owns any partner profile that is suspended.

    Safe for any user (customer, admin, anonymous): returns False unless the
    user actually has one of the partner profiles with ``status == 'suspended'``.
    """
    if user is None or not getattr(user, 'is_authenticated', True):
        return False
    for rel in _PARTNER_PROFILES:
        profile = getattr(user, rel, None)
        if profile is not None and getattr(profile, 'status', None) == 'suspended':
            return True
    return False


def is_user_suspended_supplier(user) -> bool:
    """True when ``user`` is specifically a suspended *supplier*.

    Kept for callers that need the supplier-only check (Rules 2 & 3). For the
    login / test-login gate use ``is_user_suspended_partner``.
    """
    if user is None or not getattr(user, 'is_authenticated', True):
        return False
    profile = getattr(user, 'supplier_profile', None)
    return profile is not None and profile.status == 'suspended'
