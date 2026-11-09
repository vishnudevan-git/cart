from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('distributors', '0020_rewardconfig_welcome_bonus_enabled_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='restockrequest',
            name='is_loose',
            field=models.BooleanField(
                default=False,
                help_text='True when this is a loose/bulk kg request rather than packed units',
            ),
        ),
        migrations.AddField(
            model_name='restockrequest',
            name='quantity_grams',
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                help_text='Loose quantity in grams (used when is_loose=True)',
            ),
        ),
        # variant is required for packed; make it nullable so loose requests link to product only
        migrations.AlterField(
            model_name='restockrequest',
            name='variant',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name='restock_requests',
                to='products.productvariant',
            ),
        ),
        migrations.AddField(
            model_name='restockrequest',
            name='product',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name='loose_restock_requests',
                to='products.product',
            ),
        ),
    ]
