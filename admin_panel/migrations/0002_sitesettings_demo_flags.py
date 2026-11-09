from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('admin_panel', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='demo_allow_quick_login',
            field=models.BooleanField(
                default=True,
                help_text='Allow one-click demo login links without entering credentials.'
            ),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='demo_block_transactions',
            field=models.BooleanField(
                default=True,
                help_text='Block order/payment write actions for demo sessions to avoid real transactions.'
            ),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='demo_mode_enabled',
            field=models.BooleanField(
                default=False,
                help_text='Enable no-password demo login links and walkthrough mode.'
            ),
        ),
    ]
