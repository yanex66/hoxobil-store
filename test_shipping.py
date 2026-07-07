import os
import django
import requests

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'hoxobil_store.settings')
django.setup()

from django.conf import settings

token = settings.PRINTIFY_ACCESS_TOKEN.strip()
shop_id = settings.PRINTIFY_SHOP_ID
headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}

r = requests.get(
    f'https://api.printify.com/v1/shops/{shop_id}/products.json?limit=50',
    headers=headers,
    timeout=15
)
products = r.json().get('data', [])
print(f"Testing {len(products)} products...\n")

working = []
for p in products:
    variant_id = p['variants'][0]['id'] if p.get('variants') else None
    if not variant_id:
        continue

    payload = {
        'address_to': {
            'country': 'US',
            'region': 'CA',
            'zip': '90210',
            'city': 'Beverly Hills',
            'address1': '123 Main St'
        },
        'line_items': [{
            'print_provider_id': p['print_provider_id'],
            'blueprint_id': p['blueprint_id'],
            'variant_id': variant_id,
            'quantity': 1
        }]
    }

    resp = requests.post(
        f'https://api.printify.com/v1/shops/{shop_id}/shipping/cost.json',
        headers=headers,
        json=payload,
        timeout=15
    )

    status = 'OK' if resp.status_code == 200 else 'FAIL'
    title = p['title'][:45]
    print(f"[{status}] {title} | blueprint:{p['blueprint_id']} | provider:{p['print_provider_id']} | http:{resp.status_code}")

    if resp.status_code == 200:
        working.append(p['title'])

print(f"\n--- SUMMARY ---")
print(f"Working products ({len(working)}):")
for t in working:
    print(f"  - {t}")
