from django.contrib import admin

from .models import SiteSettings


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
	list_display = (
		'site_name',
		'demo_mode_enabled',
		'demo_allow_quick_login',
		'demo_block_transactions',
		'updated_at',
	)

	fieldsets = (
		('Branding', {
			'fields': ('site_name', 'tagline', 'logo', 'footer_text'),
		}),
		('Demo Mode', {
			'fields': (
				'demo_mode_enabled',
				'demo_allow_quick_login',
				'demo_block_transactions',
			),
		}),
		('Account Lifecycle', {
			'fields': ('account_hard_delete_after_days',),
			'description': 'Days a soft-deleted account stays recoverable before it is '
			               'permanently archived (hard-deleted). See kb/09-account-lifecycle.md.',
		}),
	)

	def has_add_permission(self, request):
		# Keep singleton behavior in admin.
		if SiteSettings.objects.exists():
			return False
		return super().has_add_permission(request)
