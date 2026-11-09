def active_role(request):
    """Inject active_role and dark_mode into every template context."""
    if request.user.is_authenticated:
        role = request.session.get('active_role')
        allowed = request.user.get_roles_list()
        if not role or role not in allowed:
            role = request.user.primary_role()
            request.session['active_role'] = role

        # Apply saved language preference
        pref_lang = request.user.preferred_language
        if pref_lang and request.session.get('_language') != pref_lang:
            from django.utils.translation import activate
            activate(pref_lang)
            request.session['_language'] = pref_lang

        return {
            'active_role': role,
            'user_roles': allowed,
            # Single-role policy: an account always has exactly one role.
            'has_multiple_roles': False,
            'pending_resets': 0,
            'dark_mode': request.user.dark_mode,
        }
    return {
        'active_role': None, 'user_roles': [], 'has_multiple_roles': False,
        'pending_resets': 0, 'dark_mode': False,
    }
