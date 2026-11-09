from django.db import migrations, models


def enable_hide_out_of_stock_for_existing_distributors(apps, schema_editor):
    Distributor = apps.get_model('distributors', 'Distributor')
    Distributor.objects.all().update(hide_out_of_stock_products=True)


class Migration(migrations.Migration):

    dependencies = [
        ('distributors', '0023_distributor_hide_out_of_stock_products'),
    ]

    operations = [
        migrations.AlterField(
            model_name='distributor',
            name='hide_out_of_stock_products',
            field=models.BooleanField(
                default=True,
                help_text='If enabled, customers only see products that currently have stock.',
            ),
        ),
        migrations.RunPython(enable_hide_out_of_stock_for_existing_distributors, migrations.RunPython.noop),
    ]
