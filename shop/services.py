import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

PRINTFUL_API_URL = 'https://api.printful.com'


def get_printful_order_tracking(order_id):
    """Return normalized live fulfillment and shipment data for a Printful order."""
    token = (
        getattr(settings, 'PRINTFUL_API_KEY', '')
        or getattr(settings, 'PRINTFUL_ACCESS_TOKEN', '')
    ).strip()
    if not token:
        return {
            'status': None,
            'shipments': [],
            'error': 'Printful tracking is not configured yet.',
        }

    headers = {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/json',
        'User-Agent': 'HoxobilStore/1.0',
    }
    store_id = getattr(settings, 'PRINTFUL_STORE_ID', '')
    if store_id:
        headers['X-PF-Store-Id'] = str(store_id)

    try:
        response = requests.get(
            f'{PRINTFUL_API_URL}/orders/{order_id}',
            headers=headers,
            timeout=(10, 20),
        )
        response.raise_for_status()
        result = response.json().get('result') or {}
        shipments = []
        for shipment in result.get('shipments') or []:
            shipments.append({
                'carrier': shipment.get('carrier') or '',
                'tracking_number': shipment.get('tracking_number') or '',
                'tracking_url': shipment.get('tracking_url') or '',
                'shipped_at': shipment.get('ship_date') or '',
                'status': shipment.get('shipment_status') or '',
            })
        return {
            'status': result.get('status') or '',
            'shipments': shipments,
            'error': None,
        }
    except requests.RequestException:
        logger.exception('Printful tracking request failed for order %s', order_id)
        return {
            'status': None,
            'shipments': [],
            'error': 'Live tracking is temporarily unavailable. Please try again later.',
        }
    except (TypeError, ValueError):
        logger.exception('Invalid Printful tracking response for order %s', order_id)
        return {
            'status': None,
            'shipments': [],
            'error': 'Printful returned an invalid tracking response.',
        }
