from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0036_alter_product_master_unit'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='deleted_previous_status',
            field=models.CharField(
                blank=True,
                choices=[('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected')],
                default='',
                help_text='Approval status to restore after a soft delete.',
                max_length=10,
            ),
        ),
    ]
