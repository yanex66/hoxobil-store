from django.core.management.base import BaseCommand
import re
from decimal import Decimal

# Import Models & Components
from shop.models import Product, Category, ProductVariant 
from shop.pod_api import PodApiClient
from djmoney.money import Money

class Command(BaseCommand):
    help = 'Syncs published products from Printful with 10-digit ID Enforcement.'

    def clean_slug(self, title, max_length=150):
        s = title.lower()
        s = re.sub(r'[^a-z0-9\s-]', '', s) 
        s = re.sub(r'[\s_]+', '-', s).strip('-')
        return s[:max_length] or 'product'

    def handle(self, *args, **options):
        # Initializing client with 'PFY' to match your internal routing to Printful
        client = PodApiClient('PFY')
        self.stdout.write(self.style.NOTICE("Starting HOXOBIL Global Printful Sync..."))
        
        # Uses your client's built-in pagination handling loop
        products_data = client.sync_products() 

        if not products_data:
            self.stdout.write(self.style.ERROR("API Connection Failed or empty store data returned."))
            return
            
        total_products = 0
        MARKUP = Decimal('10.00') 
        active_pod_ids = []

        for p_data in products_data:
            # Printful product ID mapping from the individual detailed response payload
            pid = str(p_data.get('id')) 
            if not pid or pid == 'None': 
                continue
            
            active_pod_ids.append(pid)
            title = p_data.get('name', 'Unknown Product')
            main_img_url = p_data.get('thumbnail_url', '')

            try:
                # Extracts variants array injected by your client's _fetch_product_details helper method
                variants_list = p_data.get('variants', [])
                if not variants_list:
                    self.stdout.write(self.style.WARNING(f"⚠️ No active variants found for: {title}"))
                    continue

                # Process baseline prices for main structural product card display
                prices = [Decimal(str(v.get('retail_price', 0))) for v in variants_list if v.get('retail_price')]
                min_price = min(prices) + MARKUP if prices else MARKUP

                # Fetch category parameters securely
                category_id = p_data.get('main_category_id', 'Streetwear')
                tag_name = f"Category-{category_id}" if isinstance(category_id, int) else str(category_id)
                
                cat, _ = Category.objects.get_or_create(
                    name=tag_name, 
                    defaults={'slug': self.clean_slug(tag_name)}
                )
                
                # Update or generate target structural product model instance
                prod, _ = Product.objects.update_or_create(
                    pod_id=pid,
                    defaults={
                        'name': title,
                        'description': p_data.get('description', 'HOXOBIL Premium Drop Layer.'),
                        'category': cat,
                        'price': Money(amount=min_price, currency='USD'),
                        'slug': self.clean_slug(title),
                        'available': True,
                        'pod_service': 'PFY', # Keeps identification tracking values aligned
                        'image_url': main_img_url,
                    }
                )

                # Process nested child variant rows securely
                valid_variant_ids = []
                for v_data in variants_list:
                    # 10-digit explicit configuration identifier
                    vid = str(v_data.get('id'))
                    valid_variant_ids.append(vid)
                    
                    # Direct variable properties parsing (No fragile string manipulation splits)
                    size = v_data.get('size', 'OS')
                    color = v_data.get('color', 'Default')
                    
                    raw_v_price = v_data.get('retail_price', '0.00')
                    v_price = Decimal(str(raw_v_price)) + MARKUP

                    # Calculate status flags cleanly from active properties
                    is_available = v_data.get('availability_status') == 'active'

                    ProductVariant.objects.update_or_create(
                        pod_id=vid,
                        defaults={
                            'product': prod,
                            'size': size,
                            'color': color,
                            'price': Money(amount=v_price, currency='USD'),
                            'available': is_available
                        }
                    )

                # Remove structural variants no longer running live on the master channel layout
                stale_variants = prod.variants.exclude(pod_id__in=valid_variant_ids)
                for sv in stale_variants:
                    try:
                        sv.delete()
                    except Exception:
                        sv.available = False
                        sv.save()

                total_products += 1
                self.stdout.write(f"✅ Synced & Mapped (Printful Payload): {title}")

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error syncing product data model mapping {title}: {e}"))

        # Clean up stale items from your active catalog layout instances
        stale_products = Product.objects.filter(pod_service='PFY').exclude(pod_id__in=active_pod_ids)
        stale_count = stale_products.count()
        if stale_count > 0:
            self.stdout.write(self.style.WARNING(f"🧹 Cleaning up {stale_count} dead/deleted products from system cache..."))
            for sp in stale_products:
                try:
                    sp.delete()
                except Exception:
                    sp.available = False
                    sp.save()

        self.stdout.write(self.style.SUCCESS(f"HOXOBIL Database Updated. {total_products} Printful Products ready."))