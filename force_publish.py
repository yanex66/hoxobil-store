import requests
import json
import os
import django
from django.conf import settings

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'hoxobil_store.settings')
django.setup()

def emergency_publish():
    headers = {
        "Authorization": f"Bearer {settings.PRINTIFY_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    shop_id = settings.PRINTIFY_SHOP_ID
    
    # 1. Fetch all products
    print("📡 Fetching products...")
    resp = requests.get(f"https://api.printify.com/v1/shops/{shop_id}/products.json", headers=headers)
    products = resp.json().get('data', [])

    for p in products:
        p_id = p['id']
        print(f"🚀 Forcing 10-digit ID generation for: {p['title']}...")
        
        # 2. Trigger the Publish Endpoint
        pub_url = f"https://api.printify.com/v1/shops/{shop_id}/products/{p_id}/publish.json"
        
        # We tell Printify to ONLY sync the variants (this is the fastest way to get IDs)
        payload = {
            "title": True,
            "description": True,
            "images": True,
            "variants": True,
            "tags": True
        }
        
        res = requests.post(pub_url, headers=headers, json=payload)
        if res.status_code in [200, 201, 202]:
            print(f"✅ Triggered! Wait 30 seconds for {p['title']}")
        else:
            print(f"❌ Error: {res.text}")

if __name__ == "__main__":
    emergency_publish()