import re
from decimal import Decimal

from django.conf import settings
from django.db.models import Q

from .cart import Cart
from .models import Order, Product, ProductVariant
from .services import get_printful_order_tracking
from .utils import get_converted_money


TOOL_DEFINITIONS = [
    {
        'name': 'search_products',
        'description': 'Search active Hoxobil apparel and technology products.',
        'parameters': {'type': 'object', 'properties': {'query': {'type': 'string'}}, 'required': ['query']},
    },
    {
        'name': 'get_cart_contents',
        'description': (
            'Show the current customer cart. Keywords include cart, my cart, items in cart, '
            'how many products are in my cart, shopping bag, and view cart. '
            'Use this whenever the user asks about their cart, items currently selected, '
            'or quantities in their shopping session.'
        ),
        'parameters': {'type': 'object', 'properties': {}},
    },
    {
        'name': 'add_to_cart',
        'description': 'Add an available product variant to the current cart.',
        'parameters': {'type': 'object', 'properties': {'product_id': {'type': 'integer'}, 'quantity': {'type': 'integer'}}, 'required': ['product_id']},
    },
    {
        'name': 'remove_from_cart',
        'description': 'Remove a product variant from the current cart.',
        'parameters': {'type': 'object', 'properties': {'product_id': {'type': 'integer'}}, 'required': ['product_id']},
    },
    {
        'name': 'get_order_status',
        'description': 'Get a customer order and live Printful fulfillment status.',
        'parameters': {'type': 'object', 'properties': {'order_id': {'type': 'integer'}}, 'required': ['order_id']},
    },
    {'name': 'get_store_policies', 'description': 'Explain Hoxobil shipping, returns, and payments.', 'parameters': {'type': 'object', 'properties': {}}},
    {'name': 'guide_hoxobot_customization', 'description': 'Explain the Hoxobot custom apparel workflow.', 'parameters': {'type': 'object', 'properties': {}}},
]

HOXOBOT_ROUTING_PROMPT = (
    'Route commerce requests to the live tools. If the user asks about carts or cart items, '
    'including "my cart", "items in cart", "how many products are in my cart", '
    '"shopping bag", or "view cart", call get_cart_contents. '
    'If the user asks about orders or tracking, call get_order_status. '
    'If the user wants to find, browse, or search for new items, call search_products. '
    'Never invent cart, order, inventory, or tracking data.'
)


def _money(value, currency):
    converted = get_converted_money(value, currency)
    return {'amount': str(converted.amount), 'currency': converted.currency.code}


def _currency(request):
    return (request.session.get('currency') or getattr(settings, 'DEFAULT_CURRENCY', 'USD')).upper()


def _cart_summary(request):
    cart = Cart(request)
    items = []
    for item in cart:
        items.append({
            'variant_id': item['variant'].id,
            'product_id': item['product'].id,
            'name': item['product'].name,
            'size': item['variant'].size or '',
            'color': item['variant'].color or '',
            'quantity': item['quantity'],
            'unit_price': _money(item['variant'].price, _currency(request)),
            'image_url': item['product'].image_url or '',
        })
    total = cart.get_total_price()
    return {
        'items': items,
        'item_count': len(items),
        'total_quantity': sum(item['quantity'] for item in items),
        'total': {'amount': str(total.amount), 'currency': total.currency.code},
        'count': len(cart),
    }


def search_products(request, query):
    terms = [term for term in re.split(r'\s+', query.strip()) if term]
    queryset = Product.objects.filter(available=True).prefetch_related('variants')
    if terms:
        for term in terms:
            queryset = queryset.filter(
                Q(name__icontains=term) | Q(description__icontains=term) | Q(categories__name__icontains=term)
            )
    products = []
    for product in queryset.distinct()[:8]:
        variant = product.variants.filter(available=True).first()
        if not variant:
            continue
        products.append({
            'id': product.id,
            'name': product.name,
            'description': product.description[:180],
            'price': _money(variant.price, _currency(request)),
            'image_url': product.image_url or '',
            'variant_id': variant.id,
            'sizes': list(product.variants.filter(available=True).values_list('size', flat=True).distinct()),
            'colors': list(product.variants.filter(available=True).values_list('color', flat=True).distinct()),
        })
    return {'query': query, 'products': products}


def get_cart_contents(request):
    return _cart_summary(request)


def add_to_cart(request, product_id, quantity=1):
    product = Product.objects.filter(id=product_id, available=True).first()
    variant = product.variants.filter(available=True).first() if product else None
    if not variant:
        return {'error': 'That product is not currently available.'}
    Cart(request).add(variant, quantity=quantity)
    return _cart_summary(request)


def remove_from_cart(request, product_id):
    cart = Cart(request)
    variant = ProductVariant.objects.filter(id=product_id).first()
    if not variant:
        product = Product.objects.filter(id=product_id).first()
        variant = product.variants.first() if product else None
    if not variant:
        return {'error': 'That cart item could not be found.'}
    cart.remove(variant)
    return _cart_summary(request)


def get_order_status(request, order_id):
    order = Order.objects.filter(id=order_id, user=request.user).first()
    if not order:
        return {'error': 'Order not found.'}
    tracking = get_printful_order_tracking(order.pod_order_id) if order.pod_order_id else {
        'status': '', 'shipments': [], 'error': 'This order has not been submitted to fulfillment yet.',
    }
    return {
        'order_id': order.id,
        'local_status': order.status,
        'fulfillment_status': order.fulfillment_status,
        'printful_status': tracking['status'],
        'shipments': tracking['shipments'],
        'error': tracking['error'],
    }


def get_store_policies():
    return {
        'shipping': 'Hoxobil ships internationally. Delivery timing depends on destination and production status.',
        'returns': 'Contact Hoxobil support with your order reference if an item arrives damaged or incorrect.',
        'payments': 'Paystack is available for local NGN payments. Flutterwave handles supported international currencies and card payments.',
    }


def guide_hoxobot_customization():
    return {
        'steps': [
            'Choose a customizable garment from the Design Your Own collection.',
            'Select the color, size, and print placement.',
            'Tell Hoxobot whether your design uses text, an image, or both.',
            'Upload your artwork and choose typography or layout instructions.',
            'Review the generated mockup before approving production.',
        ],
    }


def detect_and_run_tool(request, message):
    text = message.lower()
    order_match = re.search(r'\border\s*#?\s*(\d+)', text)
    if order_match and ('where' in text or 'status' in text or 'track' in text):
        if not request.user.is_authenticated:
            return 'get_order_status', {'error': 'Please sign in so I can securely access your order.'}
        return 'get_order_status', get_order_status(request, int(order_match.group(1)))
    cart_request = (
        'cart' in text
        or 'shopping bag' in text
        or 'shopping basket' in text
        or bool(re.search(r'\b(items?|products?)\s+(in|inside|currently in)\s+(my\s+)?(cart|bag)\b', text))
    )
    if cart_request and not any(word in text for word in ('add', 'put', 'remove', 'delete')):
        return 'get_cart_contents', get_cart_contents(request)
    if (
        any(word in text for word in ('add', 'put'))
        and ('product' in text or 'item' in text)
        and not re.search(r'\b(?:product|item|id)\s*#?\s*\d+', text)
    ):
        return 'search_products', search_products(request, '')
    if any(word in text for word in ('search', 'find', 'show me', 'looking for', 'hoodie', 't-shirt', 'tee', 'apparel', 'drop')):
        query = re.sub(r'\b(search|find|show me|looking for)\b', '', message, flags=re.I).strip() or message
        return 'search_products', search_products(request, query)
    if any(word in text for word in ('add', 'put')) and ('cart' in text or 'bag' in text):
        match = re.search(r'\b(?:product|item|id)\s*#?\s*(\d+)', text)
        if match:
            return 'add_to_cart', add_to_cart(request, int(match.group(1)))
    if any(word in text for word in ('remove', 'delete')) and ('cart' in text or 'bag' in text):
        match = re.search(r'\b(?:product|item|id)\s*#?\s*(\d+)', text)
        if match:
            return 'remove_from_cart', remove_from_cart(request, int(match.group(1)))
    if 'policy' in text or 'shipping' in text or 'return' in text or 'payment' in text:
        return 'get_store_policies', get_store_policies()
    if 'custom' in text or 'hoxobot' in text or 'design' in text:
        return 'guide_hoxobot_customization', guide_hoxobot_customization()
    return None, None
