"""Gating for the Rent feature — the master switch, then per-role access."""
from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect

from .services import rentals_enabled


def rentals_on(view):
    """Every rent view passes through here. If the admin master switch is off,
    rentals work NOWHERE — the view is unreachable."""
    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        if not rentals_enabled():
            messages.info(request, 'Rentals are currently turned off.')
            return redirect('products:home')
        return view(request, *args, **kwargs)
    return _wrapped
