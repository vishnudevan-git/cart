from django.db import migrations, models

CATEGORY_CODES = {
    'organic-products': 'OR',
    'dry-fruits':       'DF',
    'nuts':             'NU',
    'spices':           'SP',
    'other':            'OT',
    'test-organic':     'TO',
}

def populate_category_codes(apps, schema_editor):
    Category = apps.get_model('products', 'Category')
    used = set()
    for cat in Category.objects.all():
        code = CATEGORY_CODES.get(cat.slug)
        if not code:
            code = cat.slug[:2].upper()
        base = code
        i = 1
        while code in used:
            code = (base[0] + str(i)).upper()
            i += 1
        used.add(code)
        cat.code = code
        cat.save()


def populate_skus(apps, schema_editor):
    Product = apps.get_model('products', 'Product')
    Supplier = apps.get_model('suppliers', 'Supplier')
    Category = apps.get_model('products', 'Category')

    # Ensure supplier codes exist
    for sup in Supplier.objects.all():
        if not sup.code:
            words = sup.company_name.split()
            if len(words) >= 2:
                sup.code = (words[0][0] + words[1][0]).upper()
            else:
                sup.code = sup.company_name[:2].upper()
            sup.save()

    # Generate SKUs
    counters = {}
    for product in Product.objects.all().order_by('id'):
        try:
            sup = Supplier.objects.get(pk=product.supplier_id)
            sup_code = (sup.code or 'XX')[:2].upper()
        except Supplier.DoesNotExist:
            sup_code = 'XX'

        try:
            cat = Category.objects.get(pk=product.category_id)
            cat_code = (cat.code or 'XX')[:2].upper()
        except (Category.DoesNotExist, TypeError):
            cat_code = 'XX'

        prefix = f'{sup_code}{cat_code}'
        counters[prefix] = counters.get(prefix, 0) + 1
        product.sku = f'{prefix}{counters[prefix]:04d}'
        product.save()


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0002_price_history'),
        ('suppliers', '0002_supplier_code_alter_supplier_commission_rate'),
    ]

    operations = [
        # 1. Category code — nullable first, populate, then unique
        migrations.AddField(
            model_name='category',
            name='code',
            field=models.CharField(blank=True, max_length=2, default=''),
        ),
        migrations.RunPython(populate_category_codes, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='category',
            name='code',
            field=models.CharField(blank=True, help_text='2-char code for SKU', max_length=2, unique=True),
        ),
        # 2. Product soft delete fields
        migrations.AddField(
            model_name='product',
            name='deleted_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='product',
            name='is_deleted',
            field=models.BooleanField(default=False),
        ),
        # 3. SKU — nullable first, populate, then unique
        migrations.AddField(
            model_name='product',
            name='sku',
            field=models.CharField(blank=True, db_index=True, max_length=20, default=''),
        ),
        migrations.RunPython(populate_skus, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='product',
            name='sku',
            field=models.CharField(blank=True, db_index=True, max_length=20, unique=True),
        ),
        migrations.AlterField(
            model_name='productvariant',
            name='label',
            field=models.CharField(max_length=50),
        ),
    ]
