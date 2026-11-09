"""
Stale Price Lifecycle Service Layer

This module provides domain logic for managing distributor variant prices
across single edits and bulk operations, ensuring consistency and preventing
bulk updates from bypassing stale-flag marking.
"""

from decimal import Decimal
from django.db.models import Q
from django.utils import timezone


def apply_max_percent_cap(base_price, candidate_price, cap_percent=None):
    """Clamp candidate_price by an optional max % increase over base_price."""
    if cap_percent is None:
        return candidate_price

    try:
        cap = Decimal(str(cap_percent))
    except Exception:
        return candidate_price

    if cap <= 0:
        return candidate_price

    try:
        base = Decimal(str(base_price))
        candidate = Decimal(str(candidate_price))
    except Exception:
        return candidate_price

    max_allowed = base * (Decimal('1') + (cap / Decimal('100')))
    return min(candidate, max_allowed)


def mark_distributor_prices_stale(variant_id, new_supplier_price=None):
    """
    Mark all distributor variant prices as stale for a given product variant.
    
    Fired when:
    1. Supplier edits ProductVariant.selling_price (single edit)
    2. Bulk import creates new ProductVariant (prevents bulk bypass)
    3. Any admin operation changes supplier price
    
    Args:
        variant_id: ProductVariant.pk to mark as stale
        new_supplier_price: Optional new supplier price to store for reference
    
    Returns:
        dict with 'updated_count' = number of prices marked stale
    
    Example:
        from distributors.services import mark_distributor_prices_stale
        result = mark_distributor_prices_stale(variant.pk, variant.selling_price)
        print(f"Marked {result['updated_count']} prices as stale")
    """
    from distributors.models import DistributorVariantPrice
    
    # Query all active distributor prices for this variant
    dvp_qs = DistributorVariantPrice.objects.filter(
        variant_id=variant_id,
        is_active=True,
    )
    
    update_fields = {
        'price_stale': True,
        'stale_marked_at': timezone.now(),
    }
    
    # Store reference price if provided
    if new_supplier_price is not None:
        update_fields['new_supplier_price'] = new_supplier_price
    
    updated = dvp_qs.update(**update_fields)
    
    return {
        'updated_count': updated,
        'variant_id': variant_id,
    }


def validate_margin_on_repricing(base_price, selling_price, min_margin_percent=5.0):
    """
    Validate that selling_price maintains minimum margin over base_price.
    
    Used when:
    1. Distributor saves repricing and clears stale flag
    2. Admin bulk-assigns prices
    3. System auto-pricing rules apply
    
    Args:
        base_price: Decimal, the cost price (what distributor paid)
        selling_price: Decimal, the customer selling price
        min_margin_percent: float, minimum margin % required (default 5%)
    
    Returns:
        dict with:
        - 'is_valid': bool, True if margin check passes
        - 'margin_percent': float, actual margin %
        - 'min_required': float, minimum % required
        - 'error': str or None, human-readable validation error
    
    Example:
        result = validate_margin_on_repricing(Decimal('100'), Decimal('103'))
        if not result['is_valid']:
            raise ValidationError(result['error'])
    """
    # Convert to Decimal for precise calculation
    base = Decimal(str(base_price))
    selling = Decimal(str(selling_price))
    min_margin = Decimal(str(min_margin_percent))
    
    # Edge case: base price is zero
    if base <= 0:
        return {
            'is_valid': False,
            'margin_percent': 0,
            'min_required': float(min_margin),
            'error': f'Base price must be greater than 0. Got: {base}',
        }
    
    # Calculate actual margin percentage: ((selling - base) / base) * 100
    margin = ((selling - base) / base) * Decimal('100')
    
    # Check if margin meets minimum requirement
    is_valid = margin >= min_margin
    
    error = None
    if not is_valid:
        min_required_price = base * (1 + min_margin / Decimal('100'))
        error = (
            f'Price ₹{selling} is below minimum margin. '
            f'Base: ₹{base}, Minimum margin: {min_margin}%, '
            f'Minimum price required: ₹{min_required_price.quantize(Decimal("0.01"))}'
        )
    
    return {
        'is_valid': is_valid,
        'margin_percent': float(margin),
        'min_required': float(min_margin),
        'error': error,
    }


def clear_stale_flag_on_repricing(dvp, force=False):
    """
    Clear the price_stale flag when distributor confirms/accepts new price.
    
    Includes optional margin validation gate to prevent loss-making typos.
    
    Args:
        dvp: DistributorVariantPrice instance to update
        force: bool, if True skip margin validation (use sparingly, admin-only)
    
    Returns:
        dict with:
        - 'cleared': bool, True if stale was cleared
        - 'validation_error': str or None, if validation failed
    
    Example:
        dvp = DistributorVariantPrice.objects.get(pk=1)
        result = clear_stale_flag_on_repricing(dvp)
        if result['validation_error']:
            raise ValidationError(result['validation_error'])
        dvp.save()
    """
    # Perform margin validation unless force=True
    if not force:
        validation = validate_margin_on_repricing(
            dvp.base_price,
            dvp.selling_price,
            min_margin_percent=5.0  # Configurable in future via SiteSettings
        )
        
        if not validation['is_valid']:
            return {
                'cleared': False,
                'validation_error': validation['error'],
            }
    
    # Margin check passed (or forced) — clear stale flag
    dvp.price_stale = False
    dvp.stale_marked_at = None
    dvp.new_supplier_price = None
    
    return {
        'cleared': True,
        'validation_error': None,
    }
