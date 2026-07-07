from django.conf import settings

def currency_selector(request):
    """
    Injects currency data into the template context.

    Storefront currency is independent of Printful's billing currency —
    Printful only needs USD/GBP/EUR/AUD/CAD when we pay them for
    fulfillment (handled separately in pod_api.py). The currencies we let
    *customers* browse/pay in come from our own exchange-rate config.
    """

    # 1. Supported currencies now come from our own rate table, not a POD provider
    supported_currencies = list(settings.CASH_EXCHANGE_BACKEND.get('USD', {}).keys())

    # 2. Determine the current currency
    current_currency = request.session.get('currency_code', settings.DEFAULT_CURRENCY)

    # Ensure current currency is in the supported list; if not, default.
    if current_currency not in supported_currencies:
        current_currency = settings.DEFAULT_CURRENCY

    return {
        'SUPPORTED_CURRENCIES': supported_currencies,
        'CURRENT_CURRENCY': current_currency,
    }
    
def payment_keys(request):
    """
    Injects necessary public keys for client-side payment initiation.
    """
    return {
        'FLUTTERWAVE_PUBLIC_KEY': settings.FLUTTERWAVE_PUBLIC_KEY,
        'PAYSTACK_PUBLIC_KEY': settings.PAYSTACK_PUBLIC_KEY,
    }