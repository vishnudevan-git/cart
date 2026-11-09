from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('distributors', '0025_distributor_auto_list_margin_cap_percent'),
    ]

    operations = [
        migrations.AddField(
            model_name='distributorproduct',
            name='margin_is_manual',
            field=models.BooleanField(
                default=False,
                help_text='True when distributor explicitly set manual margin for this product; False means follow global/default margin.',
            ),
        ),
    ]
