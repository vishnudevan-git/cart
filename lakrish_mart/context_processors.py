from .ui_config import (
    ADMIN_MAIN_ITEMS,
    ADMIN_PARTNER_ITEMS,
    DEFAULT_BRAND_NAME,
    DEFAULT_FONT_FAMILY,
    DEFAULT_THEME_COLOR,
    SUPPLIER_SIDEBAR_ITEMS,
    THEME_PRESET_COLORS,
)
from django.conf import settings


def ui_config(request):
    """Expose shared UI defaults/options so templates avoid hardcoded duplicates."""
    return {
        'ui_brand_name': DEFAULT_BRAND_NAME,
        'ui_default_theme_color': DEFAULT_THEME_COLOR,
        'ui_font_family': DEFAULT_FONT_FAMILY,
        'ui_theme_preset_colors': THEME_PRESET_COLORS,
        'ui_supplier_sidebar_items': SUPPLIER_SIDEBAR_ITEMS,
        'ui_admin_partner_items': ADMIN_PARTNER_ITEMS,
        'ui_admin_main_items': ADMIN_MAIN_ITEMS,
        'debug_mode': settings.DEBUG,
        'env_mode': getattr(settings, 'DB_MODE', 'prod'),
        'env_label': getattr(settings, 'ENV_LABEL', 'PROD'),
        'show_env_ribbon': getattr(settings, 'SHOW_ENV_RIBBON', True),
    }