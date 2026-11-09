from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('distributors', '0024_enable_hide_out_of_stock_for_all_distributors'),
    ]

    operations = [
        migrations.AddField(
            model_name='distributor',
            name='auto_list_margin_cap_percent',
            field=models.DecimalField(
                blank=True,
                help_text='Optional cap for per-unit auto-list pricing. Max allowed increase over base price in %.',
                max_digits=5,
                null=True,
                decimal_places=2,
            ),
        ),
    ]
