from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('distributors', '0026_distributorproduct_margin_is_manual'),
    ]

    operations = [
        migrations.AddField(
            model_name='distributor',
            name='global_pricing_config',
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='Persistent per-unit global pricing config used for distributor catalogue automation.',
            ),
        ),
    ]
