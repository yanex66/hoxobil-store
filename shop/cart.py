import logging
from django.conf import settings
from .models import ProductVariant
from decimal import Decimal
from djmoney.money import Money

logger = logging.getLogger(__name__)

CART_SESSION_ID = 'cart'


class Cart:
    def __init__(self, request):
        self.session = request.session
        cart = self.session.get(CART_SESSION_ID)
        if not cart:
            cart = self.session[CART_SESSION_ID] = {}
        self.cart = cart
        logger.debug("Cart init | session_key=%s cart=%s", self.session.session_key, self.cart)

    def save(self):
        self.session.modified = True

    def add(self, variant, quantity=1, override_quantity=False):
        variant_id = str(variant.id)
        clean_price = str(variant.price.amount)

        logger.debug(
            "Cart.add | variant_id=%s pod_id=%s qty=%s override=%s",
            variant_id, variant.pod_id, quantity, override_quantity,
        )

        if variant_id not in self.cart:
            self.cart[variant_id] = {'quantity': 0, 'price': clean_price}
            logger.debug("Cart.add | new item created for variant_id=%s", variant_id)
        else:
            # Always refresh price in case it changed since last add
            self.cart[variant_id]['price'] = clean_price

        if override_quantity:
            self.cart[variant_id]['quantity'] = int(quantity)
        else:
            self.cart[variant_id]['quantity'] += int(quantity)

        logger.debug(
            "Cart.add | variant_id=%s new_quantity=%s",
            variant_id, self.cart[variant_id]['quantity'],
        )
        self.save()

    def remove(self, variant):
        variant_id = str(variant.id)
        logger.debug("Cart.remove | variant_id=%s", variant_id)
        if variant_id in self.cart:
            del self.cart[variant_id]
            self.save()

    def __iter__(self):
        """
        Yields a copy of each cart item enriched with DB variant data.
        Never mutates self.cart so session data stays clean and serialisable.
        Prunes stale entries for variants that no longer exist in the DB.
        """
        variant_ids = list(self.cart.keys())
        if not variant_ids:
            return

        # Single DB query — evaluate immediately to avoid double-hit
        variants = list(
            ProductVariant.objects.filter(id__in=variant_ids).select_related('product')
        )
        logger.debug("Cart.__iter__ | found %d variants for ids %s", len(variants), variant_ids)

        # Build a lookup map for O(1) access
        variant_map = {str(v.id): v for v in variants}

        # Prune cart entries whose variant has been deleted from the DB
        found_ids = set(variant_map.keys())
        stale_ids = [vid for vid in variant_ids if vid not in found_ids]
        if stale_ids:
            logger.warning("Cart.__iter__ | pruning stale variant ids: %s", stale_ids)
            for vid in stale_ids:
                del self.cart[vid]
            self.save()

        for variant_id, session_item in self.cart.items():
            v = variant_map.get(variant_id)
            if v is None:
                continue

            # Work on a shallow copy — never write enriched data back into self.cart
            item = dict(session_item)
            item['variant'] = v
            item['product'] = v.product
            item['price'] = v.price
            item['total_price'] = v.price * session_item['quantity']

            logger.debug(
                "Cart.__iter__ | db_id=%s pod_id=%s name=%s",
                v.id, v.pod_id, v.product.name,
            )
            yield item

    def __len__(self):
        return sum(item['quantity'] for item in self.cart.values())

    def get_total_price(self):
        """
        Reuses __iter__ (single DB query) instead of iterating twice.
        """
        total = Decimal('0.00')
        for item in self:
            total += item['total_price'].amount
        return Money(total, settings.DEFAULT_CURRENCY)

    def clear(self):
        logger.debug("Cart.clear | clearing session cart")
        if CART_SESSION_ID in self.session:
            del self.session[CART_SESSION_ID]
            self.save()