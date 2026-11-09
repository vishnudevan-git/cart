from django.db import models


class SiteSettings(models.Model):
    """Singleton model for site-wide branding. Only one row should exist."""
    site_name = models.CharField(max_length=100, default='LaKrish Mart')
    tagline = models.CharField(max_length=200, blank=True, default='Fresh Organic Goodness Delivered to Your Door')
    logo = models.ImageField(upload_to='site/', blank=True, null=True)
    footer_text = models.CharField(max_length=300, blank=True)
    demo_mode_enabled = models.BooleanField(
        default=False,
        help_text='Enable no-password demo login links and walkthrough mode.'
    )
    demo_allow_quick_login = models.BooleanField(
        default=True,
        help_text='Allow one-click demo login links without entering credentials.'
    )
    demo_block_transactions = models.BooleanField(
        default=True,
        help_text='Block order/payment write actions for demo sessions to avoid real transactions.'
    )
    # Stage 8 — the ONE master switch for the whole Rent feature. Off = rentals
    # work nowhere (customer, marketplace, distributor). No other enable control.
    rentals_enabled = models.BooleanField(
        default=False,
        help_text='Master switch for the Rent feature. Off = rentals are disabled everywhere.'
    )
    # Account lifecycle (kb/09): a soft-deleted account is permanently archived
    # (hard-deleted) once this many days pass since it was deleted.
    account_hard_delete_after_days = models.PositiveIntegerField(
        default=2,
        help_text='Days a soft-deleted account stays recoverable before it is '
                  'permanently archived (hard-deleted) by the purge job.'
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Site Settings'

    def __str__(self):
        return self.site_name

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
