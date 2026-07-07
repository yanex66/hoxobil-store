from django.core.management.base import BaseCommand, CommandError
from shop.pod_api import PodApiClient
from shop.models import Product, ProductVariant, Category
from django.utils.text import slugify
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)

PRINTFUL_CATEGORY_MAP = {
    'bucket hat':       'Hats',
    'snapback':         'Hats',
    'beanie':           'Hats',
    'cap':              'Hats',
    'hat':              'Hats',
    'crop top':         "Women's Clothing",
    'skater dress':     "Women's Clothing",
    'dress':            "Women's Clothing",
    'sports bra':       "Women's Clothing",
    'padded bra':       "Women's Clothing",
    'bra':              "Women's Clothing",
    'skirt':            "Women's Clothing",
    'polo shirt':       "Men's Clothing",
    'polo':             "Men's Clothing",
    'crew neck':        "Men's Clothing",
    'crewneck':         "Men's Clothing",
    'sweatshirt':       "Men's Clothing",
    'hoodie':           "Men's Clothing",
    'long sleeve':      "Men's Clothing",
    'longsleeve':       "Men's Clothing",
    't-shirt':          "Men's Clothing",
    'tshirt':           "Men's Clothing",
    'unisex tee':       "Men's Clothing",
    'jogger':           'Bottoms',
    'shorts':           'Bottoms',
    'pants':            'Bottoms',
    'leggings':         'Bottoms',
}

def _get_category_for_product(name):
    name_lower = name.lower()
    for keyword, cat_name in PRINTFUL_CATEGORY_MAP.items():
        if keyword in name_lower:
            return cat_name
    return 'Uncategorized'

class Command(BaseCommand):
    help = "Synchronizes the Oksubil store catalog data natively from the Printful REST API."

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Connecting to Printful API backend..."))
        api_client = PodApiClient('PFT')
        products_data = api_client.sync_products()

        if not products_data:
            raise CommandError("Handshake failed: No products returned from Printful API wrapper.")

        active_pod_ids = []

        for item in products_data:
            pid = str(item['id'])
            active_pod_ids.append(pid)
            raw_title = item.get('name', f'product-{pid}')

            # ── "c#" prefix detection ──────────────────────────────────
            # Products created on Printful with a title starting with "c#"
            # (e.g. "c# Classic Streetwear Tee") are blanks meant to be
            # customized by customers via the HOXO chat flow, rather than
            # finished admin-designed listings. Strip the marker off the
            # title before it's ever saved/displayed, and flag the product.
            stripped_title = raw_title.strip()
            is_customizable = stripped_title.lower().startswith('c#')
            if is_customizable:
                title = stripped_title[2:].strip()
                if not title:
                    title = f'product-{pid}'
            else:
                title = raw_title

            cat_name = _get_category_for_product(title)
            category, _ = Category.objects.get_or_create(
                name=cat_name,
                defaults={'slug': slugify(cat_name)[:100]}
            )

            base_slug = slugify(title)[:180]
            slug = base_slug
            if Product.objects.filter(slug=slug).exclude(pod_id=pid).exclude(pod_id=pid).exists():
                slug = f"{base_slug}-{pid[-8:]}"

            image_url = item.get('thumbnail_url', '')
            variants_data = item.get('variants', [])
            
            prices = []
            for v in variants_data:
                try:
                    prices.append(Decimal(str(v.get('retail_price', '0'))))
                except Exception:
                    pass
            min_price = float(min(prices)) if prices else 0.00

            # Pull placement print areas
            extracted_print_zones = []
            if variants_data:
                sample_variant_id = str(variants_data[0].get('variant_id') or variants_data[0]['id'])
                catalog_details = api_client.get_catalog_variant_details(sample_variant_id)
                if catalog_details and 'result' in catalog_details:
                    placements = catalog_details['result'].get('placements', {})
                    extracted_print_zones = list(placements.keys())

            product, _ = Product.objects.update_or_create(
                pod_id=pid,
                defaults={
                    'name': title,
                    'slug': slug,
                    'available': True,
                    'image_url': image_url,
                    'pod_service': 'PFT',
                    'price': min_price,
                    'price_currency': 'USD',
                    'print_areas': extracted_print_zones,
                    'is_customizable': is_customizable,
                }
            )

            if category not in product.categories.all():
                product.categories.add(category)

            valid_variant_ids = []
            for v in variants_data:
                catalog_vid = str(v.get('variant_id') or v['id'])
                valid_variant_ids.append(catalog_vid)

                variant_name = v.get('name', '')
                name_parts = [p.strip() for p in variant_name.split(' / ')]
                size = name_parts[-1] if name_parts else ''
                color = name_parts[1] if len(name_parts) > 2 else (name_parts[0] if len(name_parts) > 1 else '')

                try:
                    price = float(Decimal(str(v.get('retail_price', '0'))))
                except Exception:
                    price = 0.00

                ProductVariant.objects.update_or_create(
                    pod_id=catalog_vid,
                    defaults={
                        'product': product,
                        'price': price,
                        'available': v.get('is_enabled', True),
                        'size': size,
                        'color': color,
                    }
                )

            # Clean up stale variants for this product
            product.variants.exclude(pod_id__in=valid_variant_ids).delete()
            tag = " [CUSTOMIZABLE BLANK]" if is_customizable else ""
            self.stdout.write(self.style.SUCCESS(f"Synced asset: {title}{tag}"))

        # Mark removed items as unavailable
        Product.objects.exclude(pod_id__in=active_pod_ids).update(available=False)
        self.stdout.write(self.style.SUCCESS("🎉 Oksubil Printful store sync complete!"))