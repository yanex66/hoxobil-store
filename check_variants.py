import os
import django
import requests

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'hoxobil_store.settings')
django.setup()

from django.conf import settings

token = getattr(settings, 'PRINTFUL_ACCESS_TOKEN', '').strip()
store_id = getattr(settings, 'PRINTFUL_STORE_ID', '')
headers = {
    'Authorization': f'Bearer {token}',
    'X-PF-Store-Id': str(store_id),
}

# Get first product
r = requests.get('https://api.printful.com/store/products?limit=5', headers=headers, timeout=10)
products = r.json().get('result', [])
p = products[0]

# Get full detail
detail = requests.get(f'https://api.printful.com/store/products/{p["id"]}', headers=headers, timeout=10).json()
variants = detail['result']['sync_variants']

print(f"Product: {p['name']}")
print(f"First 3 variants:\n")
for v in variants[:3]:
    print(f"  sync_variant id (store): {v['id']}")
    print(f"  variant_id (catalog):    {v.get('variant_id')}")
    print(f"  name:                    {v.get('name')}")
    print()
