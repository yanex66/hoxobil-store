import logging
import requests
import time
from django.conf import settings
from .models import Order, ProductVariant  # Added ProductVariant import for fast cross-referencing
from decimal import Decimal, InvalidOperation
from djmoney.money import Money

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT = 10
READ_TIMEOUT    = 60          # increased: mockup tasks can be slow
TIMEOUT         = (CONNECT_TIMEOUT, READ_TIMEOUT)

PRINTFUL_BASE_URL = 'https://api.printful.com'

# Flat shipping rate applied to custom-design-ticket cart items, since these
# are not real synced Printful products yet (no catalog variant exists) and
# therefore cannot go through Printful's live /shipping/rates API. Adjust
# this to match your actual average custom-order shipping cost.
CUSTOM_TICKET_FLAT_SHIPPING = Decimal('15.00')
CUSTOM_TICKET_SHIPPING_CURRENCY = 'USD'
CUSTOM_TICKET_POD_ID_PREFIX = 'custom-ticket-'


class PodApiClient:
    def __init__(self, service_type):
        self.service_type = service_type
        if service_type in ('PFT', 'PFY'):
            raw_token = getattr(settings, 'PRINTFUL_ACCESS_TOKEN', '')
            self.token    = raw_token.strip() if raw_token else None
            self.store_id = getattr(settings, 'PRINTFUL_STORE_ID', None)
            self.base_url = PRINTFUL_BASE_URL
            self.service  = 'Printful'
            if not self.token:
                logger.critical("PRINTFUL_ACCESS_TOKEN is missing from settings.")
            if not self.store_id:
                logger.critical("PRINTFUL_STORE_ID is missing from settings.")
        else:
            raise ValueError("Only Printful integration service types are supported.")

    def _get_headers(self, is_post=False):
        headers = {
            'Authorization': f'Bearer {self.token}',
            'Accept':        'application/json',
            'User-Agent':    'HoxobilStore/1.0',
            'X-PF-Store-Id': str(self.store_id),
        }
        if is_post:
            headers['Content-Type'] = 'application/json'
        return headers

    # ── PRODUCT SYNC ──────────────────────────────────────────────────────────

    def sync_products(self):
        all_products = []
        offset, limit = 0, 20

        while True:
            url = f"{self.base_url}/store/products?limit={limit}&offset={offset}"
            try:
                response = requests.get(url, headers=self._get_headers(), timeout=TIMEOUT)
                response.raise_for_status()
                data  = response.json()
                batch = data.get('result', [])
                if not batch:
                    break

                for p in batch:
                    detail = self._fetch_product_details(p['id'])
                    if detail:
                        all_products.append(detail)

                paging = data.get('paging', {})
                total  = paging.get('total', 0)
                offset += limit
                if offset >= total:
                    break

            except requests.Timeout:
                logger.error("sync_products | Timed out at offset %d", offset)
                break
            except requests.RequestException as e:
                logger.error("sync_products | Request error at offset %d: %s", offset, e)
                break

        logger.debug("sync_products | total products fetched: %d", len(all_products))
        return all_products

    def _fetch_product_details(self, product_id):
        url = f"{self.base_url}/store/products/{product_id}"
        try:
            resp = requests.get(url, headers=self._get_headers(), timeout=TIMEOUT)
            resp.raise_for_status()
            result  = resp.json().get('result', {})
            product = result.get('sync_product', {})
            product['variants'] = result.get('sync_variants', [])
            return product
        except requests.Timeout:
            logger.error("_fetch_product_details | Timed out for product %s", product_id)
        except requests.RequestException as e:
            logger.error("_fetch_product_details | Error for product %s: %s", product_id, e)
        return None

    # ── CATALOG HELPERS ───────────────────────────────────────────────────────

    def get_catalog_variant_details(self, catalog_variant_id):
        """Fetch details for a CATALOG variant (not a store sync variant)."""
        url = f"{self.base_url}/catalog/variants/{catalog_variant_id}"
        try:
            response = requests.get(url, headers=self._get_headers(), timeout=TIMEOUT)
            if response.status_code == 200:
                return response.json()
            logger.warning(
                "get_catalog_variant_details | status=%s for catalog_variant_id=%s",
                response.status_code, catalog_variant_id
            )
        except Exception as e:
            logger.error(
                "get_catalog_variant_details | Exception for catalog_variant_id=%s: %s",
                catalog_variant_id, e
            )
        return None

    def _resolve_catalog_ids_from_store_variant(self, store_variant_pod_id):
        """
        Resolves the Printful *catalog* product ID and *catalog* variant ID from a
        store sync-variant pod_id (the ID stored in our ProductVariant.pod_id field,
        which is actually the sync_variant's `variant_id` — i.e. the catalog variant ID).

        Strategy
        --------
        The sync command (sync_printful_products in views.py) saves
        ``v.get('variant_id') or v['id']`` as the ProductVariant.pod_id.
        ``variant_id`` on a sync_variant IS the catalog variant ID, so we can use it
        directly for /catalog/variants/{id} — no store-products round-trip needed.

        We keep the store-products fallback in case pod_id was stored as the sync
        variant's own `id` (the store-scoped integer) rather than `variant_id`.
        """
        try:
            variant = ProductVariant.objects.select_related('product').filter(
                pod_id=str(store_variant_pod_id)
            ).first()

            if not variant:
                logger.warning(
                    "_resolve_catalog_ids | No local variant found for pod_id=%s",
                    store_variant_pod_id,
                )
                return None, None

            # ── PATH 1: pod_id IS the catalog variant ID (normal post-sync state) ──
            # Try to fetch catalog details directly; this works when sync_products
            # stored variant_id (= catalog variant ID) in pod_id.
            cat_details = self.get_catalog_variant_details(str(store_variant_pod_id))
            if cat_details:
                catalog_product_id = (
                    cat_details.get('result', {})
                               .get('variant', {})
                               .get('product_id')
                )
                if catalog_product_id:
                    logger.debug(
                        "_resolve_catalog_ids | PATH1 success: catalog_product_id=%s catalog_variant_id=%s",
                        catalog_product_id, store_variant_pod_id,
                    )
                    return catalog_product_id, store_variant_pod_id

            # ── PATH 2: pod_id is the store sync_variant `id`, not `variant_id` ──
            # Walk the parent product's sync_variants to find the real catalog IDs.
            if variant.product and variant.product.pod_id:
                product_url = f"{self.base_url}/store/products/{variant.product.pod_id}"
                resp = requests.get(product_url, headers=self._get_headers(), timeout=TIMEOUT)
                if resp.status_code == 200:
                    sync_variants = resp.json().get('result', {}).get('sync_variants', [])
                    for sv in sync_variants:
                        # Match by store sync_variant id OR by our stored pod_id
                        if (
                            str(sv.get('id')) == str(store_variant_pod_id)
                            or str(sv.get('variant_id')) == str(store_variant_pod_id)
                        ):
                            catalog_variant_id = sv.get('variant_id')
                            # Resolve catalog_product_id via catalog API
                            catalog_product_id = None
                            if catalog_variant_id:
                                cat2 = self.get_catalog_variant_details(str(catalog_variant_id))
                                if cat2:
                                    catalog_product_id = (
                                        cat2.get('result', {})
                                            .get('variant', {})
                                            .get('product_id')
                                    )
                            if catalog_product_id and catalog_variant_id:
                                logger.debug(
                                    "_resolve_catalog_ids | PATH2 success: catalog_product_id=%s catalog_variant_id=%s",
                                    catalog_product_id, catalog_variant_id,
                                )
                                return catalog_product_id, catalog_variant_id
                else:
                    logger.warning(
                        "_resolve_catalog_ids | Store product fetch returned %s for pod_id=%s",
                        resp.status_code, variant.product.pod_id,
                    )

        except Exception as e:
            logger.error(
                "_resolve_catalog_ids | Exception for store_variant_pod_id=%s: %s",
                store_variant_pod_id, e,
            )
        return None, None

    def get_valid_placement(self, catalog_product_id, requested_zone):
        """
        Fetches available placements + printfile dimensions for a catalog product.
        Returns (validated_zone, pf_width, pf_height).
        """
        url = f"{self.base_url}/mockup-generator/printfiles/{catalog_product_id}"
        try:
            response = requests.get(url, headers=self._get_headers(), timeout=TIMEOUT)
            if response.status_code == 200:
                result     = response.json().get('result', {})
                available  = result.get('available_placements', {})
                printfiles = result.get('printfiles', [])

                if not available:
                    return requested_zone, 1800, 1800

                if requested_zone in available:
                    matched = requested_zone
                else:
                    matched = None
                    for key in available:
                        if requested_zone in key or key in requested_zone:
                            matched = key
                            break
                    if not matched:
                        matched = list(available.keys())[0]

                width, height = 1800, 1800
                if printfiles:
                    width  = printfiles[0].get('width',  1800)
                    height = printfiles[0].get('height', 1800)

                return matched, width, height
        except Exception as e:
            logger.error(
                "get_valid_placement | Error for catalog_product_id=%s: %s",
                catalog_product_id, e
            )
        return requested_zone, 1800, 1800

    # ── MOCKUP GENERATOR ──────────────────────────────────────────────────────

    def generate_mockup_preview(
        self,
        product_variant_id,
        file_url,
        placement_zone='front',
        custom_position=None,
        rotation=0
    ):
        """
        Full pipeline:
          1. Resolve catalog_product_id + catalog_variant_id from the store variant ID
          2. Validate / remap the placement zone
          3. Submit a mockup-generator task
          4. Poll until completed and return (mockup_url, pf_width, pf_height)
        Returns (None, None, None) on failure.
        """
        # ── STEP 1: Resolve catalog IDs ───────────────────────────────────────
        catalog_product_id, catalog_variant_id = self._resolve_catalog_ids_from_store_variant(
            product_variant_id
        )

        if not catalog_product_id or not catalog_variant_id:
            logger.error(
                "generate_mockup_preview | Could not resolve catalog IDs for "
                "store_variant_pod_id=%s", product_variant_id
            )
            return None, None, None

        # ── STEP 2: Validate placement + get printfile dimensions ─────────────
        raw_zone = placement_zone.lower().replace(' ', '_')
        validated_zone, pf_width, pf_height = self.get_valid_placement(
            catalog_product_id, raw_zone
        )
        logger.debug(
            "generate_mockup_preview | real printfile dims: pf_width=%s pf_height=%s zone=%s",
            pf_width, pf_height, validated_zone
        )

        # ── STEP 3: Build position payload safely mapping types ───────────────
        if custom_position and isinstance(custom_position, dict):
            position = {
                'area_width':  int(custom_position.get('area_width', pf_width)),
                'area_height': int(custom_position.get('area_height', pf_height)),
                'width':       int(custom_position.get('width', pf_width)),
                'height':      int(custom_position.get('height', pf_height)),
                'top':         int(custom_position.get('top', 0)),
                'left':        int(custom_position.get('left', 0)),
            }
        else:
            position = {
                'area_width':  pf_width,
                'area_height': pf_height,
                'width':       pf_width,
                'height':      pf_height,
                'top':         0,
                'left':        0,
            }

        file_entry = {
            'placement': validated_zone,
            'image_url': file_url,
            'position':  position,
        }

        try:
            val_rot = int(rotation)
            if val_rot != 0:
                file_entry['rotation'] = val_rot
        except (ValueError, TypeError):
            pass

        payload = {
            'variant_ids': [int(catalog_variant_id)],
            'format':      'jpg',
            'files':       [file_entry],
        }

        url = f"{self.base_url}/mockup-generator/create-task/{catalog_product_id}"

        # ── STEP 4: Submit task (with 429 retry backoff) ──────────────────────
        try:
            response = None
            for attempt in range(4):  # up to 3 retries
                response = requests.post(
                    url,
                    headers=self._get_headers(is_post=True),
                    json=payload,
                    timeout=TIMEOUT,
                )
                if response.status_code == 429:
                    wait = 2 ** attempt  # 1 s, 2 s, 4 s
                    logger.warning(
                        "generate_mockup_preview | 429 rate-limited by Printful, "
                        "retrying in %ds (attempt %d/3)", wait, attempt + 1
                    )
                    time.sleep(wait)
                    continue
                break  # success or non-429 error — stop retrying

            response.raise_for_status()
            task_key = response.json().get('result', {}).get('task_key')

            if not task_key:
                logger.error("generate_mockup_preview | No task_key in response.")
                return None, None, None

            # ── STEP 5: Poll for result (up to 10 × 3 s = 30 s) ─────────────
            status_url = f"{self.base_url}/mockup-generator/task?task_key={task_key}"
            for attempt in range(10):
                time.sleep(3)
                status_resp = requests.get(
                    status_url, headers=self._get_headers(), timeout=TIMEOUT
                )
                data   = status_resp.json()
                status = data.get('result', {}).get('status')

                if status == 'completed':
                    mockups = data.get('result', {}).get('mockups', [])
                    if mockups:
                        return mockups[0].get('mockup_url'), pf_width, pf_height
                    return None, None, None
                elif status == 'failed':
                    logger.error("generate_mockup_preview | Task failed on Printful core.")
                    return None, None, None

        except Exception as e:
            logger.error("generate_mockup_preview | Exception: %s", e)

        return None, None, None

    # ── SHIPPING ──────────────────────────────────────────────────────────────

    @staticmethod
    def _is_custom_ticket_pod_id(pod_id):
        """Custom design tickets are stored in the cart with pod_id values like
        'custom-ticket-7' — these are NOT real Printful catalog variants and
        must never be sent to Printful's live shipping/mockup APIs."""
        return str(pod_id).startswith(CUSTOM_TICKET_POD_ID_PREFIX)

    def get_detailed_shipping_rates(
        self, cart_items, country, zip_code, state="", city="", address1=""
    ):
        """
        Splits cart items into:
          - real Printful products  -> queried live against Printful's API
          - custom design tickets   -> given a flat manual shipping rate
        and returns a combined list of rate options.

        Previously, custom-ticket items (pod_id like 'custom-ticket-7') failed
        `int(variant_obj.pod_id)` and were silently dropped. If the cart
        contained ONLY a custom ticket, this left an empty `line_items` list,
        Printful returned nothing usable, and the checkout page showed
        "Could not load shipping rate". Now custom tickets are routed to a
        flat shipping rate instead of Printful's live rates API.
        """
        line_items = []
        has_custom_ticket = False

        for item in cart_items:
            variant_obj = item.get('variant')
            if not variant_obj:
                continue

            pod_id = getattr(variant_obj, 'pod_id', None)

            if pod_id is not None and self._is_custom_ticket_pod_id(pod_id):
                has_custom_ticket = True
                continue

            try:
                line_items.append({
                    'variant_id': int(pod_id),
                    'quantity':   item['quantity'],
                })
            except (ValueError, TypeError):
                # Genuinely malformed/unexpected pod_id — skip it but log,
                # since this is NOT the expected custom-ticket case.
                logger.warning(
                    "get_detailed_shipping_rates | Skipping cart item with "
                    "non-numeric, non-custom-ticket pod_id=%r", pod_id
                )
                continue

        rates = []

        # ── Live Printful rates for any real product line items ──
        if line_items:
            payload = {
                'recipient': {
                    'country_code': country,
                    'state_code':   state or '',
                    'zip':          str(zip_code),
                    'city':         city or '',
                    'address1':     address1 or '',
                },
                'items': line_items,
            }
            url = f"{self.base_url}/shipping/rates"
            try:
                response = requests.post(
                    url, headers=self._get_headers(is_post=True), json=payload, timeout=TIMEOUT
                )
                data = response.json()
                if response.status_code == 200:
                    for rate in data.get('result', []):
                        try:
                            price = Decimal(str(rate['rate']))
                            rates.append({
                                'id':       rate.get('id', 'STANDARD'),
                                'name':     rate.get('name', 'Standard Shipping'),
                                'price':    price,
                                'currency': rate.get('currency', 'USD'),
                                'eta':      f"{rate.get('minDeliveryDays', '?')}-{rate.get('maxDeliveryDays', '?')} business days",
                            })
                        except (InvalidOperation, KeyError):
                            continue
                else:
                    logger.error(
                        "get_detailed_shipping_rates | Printful returned status=%s body=%s",
                        response.status_code, data,
                    )
            except Exception as e:
                logger.error("get_detailed_shipping_rates | Error: %s", e)

        # ── Flat rate for custom design tickets ──
        if has_custom_ticket:
            if rates:
                # Mix of real products + a custom ticket: add the flat custom
                # shipping cost on top of each live Printful rate option so the
                # customer still sees a single combined total per option.
                for rate in rates:
                    rate['price'] = rate['price'] + CUSTOM_TICKET_FLAT_SHIPPING
                    rate['name']  = f"{rate['name']} (incl. custom item)"
            else:
                # Cart is ONLY custom design ticket(s) — no Printful call needed.
                rates.append({
                    'id':       'CUSTOM_FLAT',
                    'name':     'Standard Shipping (Custom Order)',
                    'price':    CUSTOM_TICKET_FLAT_SHIPPING,
                    'currency': CUSTOM_TICKET_SHIPPING_CURRENCY,
                    'eta':      '7-14 business days',
                })

        return rates

    def get_shipping_rate(
        self, cart_items, country, zip_code, state="", city="", address1=""
    ):
        rates = self.get_detailed_shipping_rates(
            cart_items, country, zip_code, state, city, address1
        )
        if rates:
            return Money(amount=rates[0]['price'], currency=rates[0].get('currency', 'USD'))
        return Money(amount=Decimal('0.00'), currency='USD')

    # ── ORDER CREATION ────────────────────────────────────────────────────────

    def create_order(self, order: Order):
        line_items = []
        for item in order.items.all():
            pod_id = item.product_variant.pod_id
            if self._is_custom_ticket_pod_id(pod_id):
                # Custom design tickets are submitted to Printful separately
                # (see custom_order_payment_callback in views.py, which uses
                # ticket.printful_product_id) — skip them here.
                continue
            try:
                line_items.append({
                    'variant_id': int(pod_id),
                    'quantity':   item.quantity,
                })
            except (ValueError, TypeError):
                continue

        if not line_items:
            return {'error': 'No valid line items.'}

        payload = {
            'recipient': {
                'name':         f"{order.first_name} {order.last_name}",
                'address1':     order.address,
                'city':         order.city,
                'state_code':   order.state or '',
                'zip':          str(order.postal_code),
                'country_code': order.country,
                'email':        order.email,
                'phone':        order.phone or '',
            },
            'items': line_items,
            'retail_costs': {
                'shipping':              str(order.shipping_cost.amount),
                'retail_delivery_cost':  str(order.shipping_cost.amount),
            },
        }

        url = f"{self.base_url}/orders"
        try:
            response = requests.post(
                url, headers=self._get_headers(is_post=True), json=payload, timeout=TIMEOUT
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error("create_order | Error: %s", e)
            return {'error': str(e)}