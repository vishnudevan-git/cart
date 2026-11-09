from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('distributors', '0022_distributorvariantprice_stale_marked_at_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='distributor',
            name='hide_out_of_stock_products',
            field=models.BooleanField(
                default=False,
                help_text='If enabled, customers only see products that currently have stock.',
            ),
        ),
    ]
