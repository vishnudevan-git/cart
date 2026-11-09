from functools import wraps

from django.shortcuts import redirect


def ensure_user_role(
    request,
    required_roles,
    *,
    set_active_role=None,
    allow_superuser=False,
    redirect_to='accounts:login',
):
    """Return redirect response when role access fails, else None."""
    if not request.user.is_authenticated:
        return redirect(redirect_to)

    allowed_roles = request.user.get_roles_list()
    has_role = any(role in allowed_roles for role in required_roles)
    if not has_role and not (allow_superuser and request.user.is_superuser):
        return redirect(redirect_to)

    if set_active_role:
        request.session['active_role'] = set_active_role

    return None


def role_required(
    *required_roles,
    set_active_role=None,
    allow_superuser=False,
    redirect_to='accounts:login',
):
    """Decorator factory for reusable role-based gate checks."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            denial = ensure_user_role(
                request,
                required_roles,
                set_active_role=set_active_role,
                allow_superuser=allow_superuser,
                redirect_to=redirect_to,
            )
            if denial:
                return denial
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator
