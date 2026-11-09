import os, django
os.environ['DJANGO_SETTINGS_MODULE'] = 'lakrish_mart.settings'
django.setup()
from products.models import Product
from django.db.models import Count

dupes = Product.objects.values('name').annotate(c=Count('id')).filter(c__gt=1).order_by('-c')
if dupes:
    for d in dupes:
        print(f"{d['c']}x  {d['name']}")
        for p in Product.objects.filter(name=d['name']).select_related('supplier'):
            print(f"     -> {p.supplier.company_name}")
else:
    print("No products share the same name across suppliers.")
    print("All product names:")
    for p in Product.objects.select_related('supplier').order_by('name')[:20]:
        print(f"  {p.name}  ({p.supplier.company_name})")
