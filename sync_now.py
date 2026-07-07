import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'hoxobil_store.settings')
django.setup()

from shop.pod_api import PodApiClient
from shop.models import Product, ProductVariant, Category

client = PodApiClient('PFY')
print('Fetching products from Printify...')
products_data = client.sync_products()
print(f'Got {len(products_data)} products')

if not products_data:
    print('ERROR: No products returned. Check your token and network.')
else:
    for item in products_data:
        pid = str(item['id'])
        tags = item.get('tags', [])
        cat_name = tags[0] if tags else 'Uncategorized'
        category, _ = Category.objects.get_or_create(name=cat_name)

        variants_raw = item.get('variants', [])
        available_prices = [
            float(v['price']) / 100
            for v in variants_raw
            if v.get('is_available', True) and v.get('price')
        ]
        product_price = min(available_prices) if available_prices else 0.00

        product, created = Product.objects.update_or_create(
            pod_id=pid,
            defaults={
                'category': category,
                'name': item['title'],
                'available': True,
                'price': product_price,
                'price_currency': 'USD',
            }
        )

        for v in variants_raw:
            vid = str(v['id'])
            title_parts = v.get('title', 'Default').split(' / ')
            size = title_parts[0]
            color = title_parts[1] if len(title_parts) > 1 else 'Default'
            ProductVariant.objects.update_or_create(
                pod_id=vid,
                defaults={
                    'product': product,
                    'price': float(v['price']) / 100,
                    'price_currency': 'USD',
                    'available': v.get('is_available', True),
                    'size': size,
                    'color': color,
                }
            )

        status = 'CREATED' if created else 'UPDATED'
        print(f'[{status}] {item["title"]}')

    print('\nDONE — all products synced successfully.')
