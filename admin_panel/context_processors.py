from django.conf import settings
from lakrish_mart.ui_config import DEFAULT_THEME_COLOR


def site_branding(request):
    """
    Inject branding into every template.
    - Customers see their distributor's branding (if set), else site default.
    - Everyone else sees site default.
    """
    from admin_panel.models import SiteSettings
    site = SiteSettings.get()

    brand_name = site.site_name
    brand_logo = site.logo
    brand_tagline = site.tagline
    theme_color = getattr(settings, 'THEME_COLOR', DEFAULT_THEME_COLOR)

    # Override with distributor branding for customers
    dist = None
    if request.user.is_authenticated:
        active_role = request.session.get('active_role', request.user.role)
        if active_role == 'customer':
            dist = getattr(request.user, 'distributor', None)
        elif active_role == 'distributor':
            try:
                dist = request.user.distributor_profile
            except Exception:
                pass

    # Also check session-based distributor (for guests via invite link)
    if not dist:
        dist_id = request.session.get('distributor_id')
        if dist_id:
            from distributors.models import Distributor
            try:
                dist = Distributor.objects.get(pk=dist_id, status='active')
            except Distributor.DoesNotExist:
                pass

    if dist:
        if dist.company_name:
            brand_name = dist.company_name
        if dist.logo:
            brand_logo = dist.logo
        if dist.tagline:
            brand_tagline = dist.tagline
        if dist.theme_color:
            theme_color = dist.theme_color

    # Build store home URL for guests browsing via distributor store link
    store_home_url = '/'
    if dist and not request.user.is_authenticated:
        from django.urls import reverse
        store_home_url = reverse('products:distributor_storefront', args=[dist.invite_code])

    # Current URL name (e.g. 'user_list', 'customer_list') so the admin sidebar
    # can highlight the active item AND keep the "Partners" group expanded on
    # the pages it contains. Without this, url_name was always empty and the
    # group collapsed on every navigation.
    url_name = ''
    try:
        if request.resolver_match:
            url_name = request.resolver_match.url_name or ''
    except Exception:
        url_name = ''

    return {
        'brand_name': brand_name,
        'brand_logo': brand_logo,
        'brand_tagline': brand_tagline,
        'theme_color': theme_color,
        'store_home_url': store_home_url,
        'site_settings': site,
        'url_name': url_name,
    }
